"""Native worktrees owned by durable run/lane records. No cleanup or scheduling."""
from pathlib import Path
import os
import subprocess
from string import Formatter
from uuid import uuid4


def git(repository, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(repository), *args],
                            env=env, capture_output=True, text=True)
    if result.returncode:
        raise ValueError('git worktree operation failed: ' + result.stderr.strip())
    return result.stdout.strip()


def checked_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('worktree/repository path must stay inside project')
    target = root / path
    if any(part.is_symlink() for part in (target, *target.parents)):
        raise ValueError('symlink worktree/repository path forbidden')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('worktree/repository path escapes project')
    return target


def identity(path):
    info = Path(path).stat()
    return [info.st_dev, info.st_ino]


def registrations(repository):
    # -z keeps paths with whitespace/newlines unambiguous.
    output = git(repository, 'worktree', 'list', '--porcelain', '-z')
    records = []
    for block in output.split('\0\0'):
        record = {}
        for field in block.split('\0'):
            key, _, value = field.partition(' ')
            if key:
                record[key] = value
        if record:
            records.append(record)
    return records


def verify(record):
    path = Path(record['path']); repository = Path(record['repository'])
    if (path / '.git').is_symlink() or (repository / '.git').is_symlink():
        raise ValueError('symlink Git metadata forbidden')
    common = git(repository, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if any(part.is_symlink() for part in (Path(common), *Path(common).parents)):
        raise ValueError('symlink repository metadata forbidden')
    if common != record['repository_id'] or identity(common) != record['repository_identity']:
        raise ValueError('repository ownership mismatch')
    matches = [entry for entry in registrations(repository) if entry.get('worktree') == str(path)]
    if len(matches) != 1 or matches[0].get('branch') != 'refs/heads/' + record['branch']:
        raise ValueError('worktree registration/branch ownership mismatch')
    if git(path, 'rev-parse', '--show-toplevel') != str(path):
        raise ValueError('worktree root mismatch')
    if git(path, 'rev-parse', '--path-format=absolute', '--git-common-dir') != common:
        raise ValueError('worktree belongs to wrong repository')
    admin = Path(git(path, 'rev-parse', '--absolute-git-dir'))
    if any(part.is_symlink() for part in (admin, *admin.parents)) or (admin / 'gitdir').is_symlink() or not admin.is_relative_to(Path(common) / 'worktrees'):
        raise ValueError('worktree administrative ownership mismatch')
    if Path((admin / 'gitdir').read_text().strip()) != path / '.git':
        raise ValueError('worktree administrative backlink mismatch')
    observed = {'git_dir': str(admin), 'git_dir_identity': identity(admin), 'path_identity': identity(path)}
    if any(key in record and record[key] != value for key, value in observed.items()):
        raise ValueError('worktree ownership identity changed')
    return observed


def plan(state, config):
    project = Path(state['project'])
    repositories = config.repos if config.repos is not None else ['.']
    if not repositories:
        raise ValueError('mutable work requires at least one repository')
    repos = []
    for relative in repositories:
        source = project if relative == '.' else checked_path(project, relative)
        if any(part.is_symlink() for part in (source, *source.parents)):
            raise ValueError('symlink repository path forbidden')
        if git(source, 'rev-parse', '--show-toplevel') != str(source):
            raise ValueError('configured repository must be an exact Git root')
        common = git(source, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        if (source / '.git').is_symlink() or any(part.is_symlink() for part in (Path(common), *Path(common).parents)):
            raise ValueError('symlink repository metadata forbidden')
        if any(existing['repository_id'] == common for existing in repos):
            raise ValueError('duplicate repository identity')
        base = config.base_branch if config.base_branch is not None else 'HEAD'
        if not base or base.startswith('-'):
            raise ValueError('invalid base_branch')
        commit = git(source, 'rev-parse', '--verify', '--end-of-options', base + '^{commit}')
        repos.append({'repository': str(source), 'repository_id': common,
                      'repository_identity': identity(common), 'base_commit': commit})
    pattern = config.worktree_pattern if config.worktree_pattern is not None else '.crew/worktrees/{run}/{lane}/{repo}'
    try:
        specifications = [pattern]
        while specifications:
            for _, field, spec, _ in Formatter().parse(specifications.pop()):
                if field is not None:
                    if field not in {'run', 'lane', 'repo'}:
                        raise ValueError('unsupported format field')
                    if spec:
                        specifications.append(spec)
    except ValueError as error:
        raise ValueError('unsupported worktree_pattern') from error
    paths = []
    lanes = []
    for lane in state['lanes']:
        records = []
        for index, repo in enumerate(repos):
            try:
                relative = pattern.format(run=state['run_id'], lane=lane['id'], repo=f'repo{index}')
            except (KeyError, IndexError, ValueError, AttributeError) as error:
                raise ValueError('unsupported worktree_pattern') from error
            target = checked_path(project, relative)
            if target == project or any(target == prior or target.is_relative_to(prior) or prior.is_relative_to(target) for prior in paths):
                raise ValueError('worktree path collision')
            if target.exists() or any(entry.get('worktree') == str(target) for repo_ in repos for entry in registrations(repo_['repository'])):
                raise ValueError('existing unowned worktree path/registration')
            if target == Path(repo['repository']) or Path(repo['repository']).is_relative_to(target):
                raise ValueError('worktree path contains repository')
            paths.append(target)
            records.append({**repo, 'path': str(target), 'branch': 'crew/' + uuid4().hex, 'status': 'planned'})
        lanes.append({**lane, 'worktrees': records, 'launch_cwd': records[0]['path']})
    return lanes


def prepare_lane(store, config, item, *, retry=False):
    """Caller holds runtime lock. Persist plan and create intent before native Git."""
    state = store.read_state()
    matches = [lane for lane in state['lanes'] if item in lane['items']]
    if len(matches) != 1:
        raise ValueError('mutable item requires one exact lane')
    if not any('worktrees' in lane for lane in state['lanes']):
        lanes = plan(state, config)
        store.update_state(lambda current: {**current, 'lanes': lanes})
        state = store.read_state()
    lane = next(lane for lane in state['lanes'] if item in lane['items'])
    if not lane.get('worktrees') or lane.get('launch_cwd') != lane['worktrees'][0]['path']:
        raise ValueError('lane ownership metadata missing or inconsistent')
    def save(index, **values):
        def update(current):
            owned = next(entry for entry in current['lanes'] if entry['id'] == lane['id'])
            owned['worktrees'][index].update(values)
            return current
        store.update_state(update)
        lane['worktrees'][index].update(values)
    for index, record in enumerate(lane['worktrees']):
        path = checked_path(Path(state['project']), os.path.relpath(record['path'], state['project']))
        if str(path) != record['path']:
            raise ValueError('owned worktree path changed')
        if record['status'] in ('creating', 'uncertain') and not retry:
            raise ValueError('worktree creation uncertain; explicit dispatch retry required')
        if record['status'] == 'ready' or record['status'] in ('creating', 'uncertain') and path.exists():
            observed = verify(record)
            save(index, status='ready', **observed)
            continue
        if record['status'] not in ('planned', 'creating', 'uncertain'):
            raise ValueError('unknown worktree ownership status')
        common = git(record['repository'], 'rev-parse', '--path-format=absolute', '--git-common-dir')
        if common != record['repository_id'] or identity(common) != record['repository_identity']:
            raise ValueError('repository ownership mismatch')
        if path.exists() or any(entry.get('worktree') == str(path) for entry in registrations(record['repository'])):
            raise ValueError('existing unowned worktree location')
        refs = git(record['repository'], 'for-each-ref', '--format=%(refname)', 'refs/heads/' + record['branch'])
        if refs:
            raise ValueError('owned branch exists without exact worktree; retained for inspection')
        path.parent.mkdir(parents=True, exist_ok=True)
        save(index, status='creating')
        try:
            git(record['repository'], 'worktree', 'add', '-b', record['branch'], str(path), record['base_commit'])
            save(index, status='ready', **verify(record))
        except Exception as error:
            # Never delete a partial result or infer failure from command outcome alone.
            try:
                observed = verify(record)
            except Exception:
                save(index, status='uncertain', error=str(error))
                raise ValueError('worktree creation uncertain; retained for explicit inspection/retry') from error
            save(index, status='ready', error=str(error), **observed)
    return lane


def cleanup_worktrees(store, *, keep=False):
    """Remove only clean exact-owned worktrees after every known writer is closed.

    Caller holds the runtime lock. State is rechecked after Git observations and
    locked through removal. Branches and uncertain outcomes are always retained.
    """
    import copy
    results = []

    def writers(state):
        resources = list(state['tasks'].values()) + ([state['orchestrator']] if state.get('orchestrator') else [])
        return (state.get('start_uncertain') or state.get('owned_panes')
                or any((r.get('launch_intent') or r.get('pane_id')) and not r.get('closed') for r in resources))

    def absent(record):
        # An absent path alone is never proof of a previously attempted removal.
        path = Path(record['path'])
        common = git(record['repository'], 'rev-parse', '--path-format=absolute', '--git-common-dir')
        if common != record['repository_id'] or identity(common) != record['repository_identity']:
            return False
        return (not path.exists() and not path.is_symlink()
                and not Path(record['git_dir']).exists()
                and not any(e.get('worktree') == str(path) for e in registrations(record['repository'])))

    snapshot = store.read_state()
    for lane_index, lane in enumerate(snapshot['lanes']):
        for record_index, initial in enumerate(lane.get('worktrees', [])):
            state = store.read_state()
            record = copy.deepcopy(state['lanes'][lane_index]['worktrees'][record_index])
            result = {'lane': lane['id'], 'path': record['path'], 'status': 'retained', 'reason': ''}
            def save(**values):
                def update(current):
                    item = current['lanes'][lane_index]['worktrees'][record_index]
                    if item == record:
                        item.update(values)
                    return current
                store.update_state(update)
            try:
                path = checked_path(Path(state['project']), os.path.relpath(record['path'], state['project']))
                if str(path) != record['path']:
                    raise ValueError('owned worktree path changed')
                if record.get('cleanup_status') in ('removing', 'removed') and absent(record):
                    result.update(status='removed', reason='exact prior removal reconciled')
                    save(cleanup_status='removed')
                elif keep:
                    result['reason'] = '--keep-worktrees requested'
                elif writers(state):
                    result['reason'] = 'active or uncertain processes retained'
                else:
                    required = ('repository_id', 'repository_identity', 'path_identity', 'git_dir', 'git_dir_identity', 'base_commit')
                    if record.get('status') != 'ready' or any(k not in record for k in required):
                        raise ValueError('worktree ownership incomplete or uncertain')
                    if sum(r.get('path') == record['path'] or
                           (r.get('repository_id') == record['repository_id'] and r.get('branch') == record['branch'])
                           for l in state['lanes'] for r in l.get('worktrees', [])) != 1:
                        raise ValueError('shared worktree ownership retained')
                    verify(record)
                    if git(path, 'status', '--porcelain', '--untracked-files=all', '--ignored'):
                        raise ValueError('dirty or ignored worktree content retained')
                    if git(path, 'rev-parse', 'HEAD') != record['base_commit']:
                        raise ValueError('changed worktree HEAD retained')
                    # Persist attempt only after exact authoritative state revalidation.
                    def claim(current):
                        if current != state or writers(current) or not current.get('close_requested'):
                            raise ValueError('authoritative ownership, control or writer state changed')
                        current['lanes'][lane_index]['worktrees'][record_index]['cleanup_status'] = 'removing'
                        return current
                    claimed = store.update_state(claim)
                    record = copy.deepcopy(claimed['lanes'][lane_index]['worktrees'][record_index])
                    with store._lock():
                        current = store._read_unlocked()
                        if current != claimed or writers(current):
                            raise ValueError('authoritative ownership or writer state changed before remove')
                        verify(record)
                        if git(path, 'status', '--porcelain', '--untracked-files=all', '--ignored'):
                            raise ValueError('dirty or ignored worktree content retained')
                        if git(path, 'rev-parse', 'HEAD') != record['base_commit']:
                            raise ValueError('changed worktree HEAD retained')
                        error = None
                        try:
                            git(record['repository'], 'worktree', 'remove', str(path))
                        except (OSError, ValueError) as caught:
                            error = caught
                        if absent(record):
                            current['lanes'][lane_index]['worktrees'][record_index]['cleanup_status'] = 'removed'
                            store._write_unlocked(current)
                            result.update(status='removed', reason='clean exact-owned worktree removed; branch retained')
                        else:
                            raise ValueError(str(error) if error else 'removal outcome uncertain; retained')
            except (OSError, ValueError, KeyError) as error:
                result['reason'] = str(error)
            results.append(result)
    return results
