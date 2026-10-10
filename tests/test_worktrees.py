"""Real temporary Git fixtures; fake Herdr only."""
from pathlib import Path
import subprocess
import pytest
import yaml
from test_config import _global_config, _write_yaml
from test_runtime_cli import FakeHerdr, ready
from crew.runtime import Runtime, create_run


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def repository(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, 'init', '-b', 'main')
    (path / 'source.txt').write_text('original\n')
    git(path, 'add', 'source.txt')
    git(path, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
    return path


@pytest.fixture
def mutable(tmp_path, monkeypatch):
    root = _global_config(tmp_path / 'global')
    roles = yaml.safe_load((root / 'roles.yaml').read_text()); roles['qa']['readonly'] = False
    _write_yaml(root / 'roles.yaml', roles)
    project = repository(tmp_path / 'project')
    monkeypatch.setenv('CREW_ROLE', 'orchestrator')
    return root, project


def run(mutable, **config):
    root, project = mutable
    _write_yaml(project / '.crew/project.yaml', config)
    runtime = Runtime(create_run(root, project, 'run1', 'readonly', ['A', 'B'], groups=2))
    adapter = FakeHerdr(); adapter.cwds = []
    original = adapter.create_tab
    def create_tab(workspace, cwd, label, **kwargs):
        task = next(t for t in runtime.store.read_state()['tasks'].values() if t.get('launch_cwd') == cwd and 'launch_intent' in t)
        assert task['worktree_lane']
        adapter.cwds.append(cwd)
        return original(workspace, cwd, label, **kwargs)
    adapter.create_tab = create_tab
    runtime.start(herdr=adapter)
    return runtime, adapter


def first(runtime, adapter):
    runtime.tick(herdr=adapter, resource_check=ready)
    state = runtime.store.read_state()
    return next(iter(state['tasks'])), state


def test_isolation_cwd_serialization_dirty_survival(mutable):
    runtime, adapter = run(mutable)
    task_id, state = first(runtime, adapter); task = state['tasks'][task_id]
    cwd = Path(task['launch_cwd'])
    assert cwd != mutable[1] and adapter.cwds == [str(cwd)]
    assert sum(t['status'] == 'running' for t in state['tasks'].values()) == 1
    (cwd / 'source.txt').write_text('lane one\n')
    assert (mutable[1] / 'source.txt').read_text() == 'original\n'
    (runtime.path / task['artifact']).write_text('finished\nQA DONE\n')
    runtime.tick(herdr=adapter, resource_check=ready)
    other = next(t for key, t in runtime.store.read_state()['tasks'].items() if key != task_id)
    second = Path(other['launch_cwd'])
    assert second != cwd and (second / 'source.txt').read_text() == 'original\n'
    assert git(cwd, 'rev-parse', '--show-toplevel') == str(cwd)
    assert git(second, 'rev-parse', '--show-toplevel') == str(second)
    runtime.request_control('close'); runtime.tick(herdr=adapter, resource_check=ready)
    assert (cwd / 'source.txt').read_text() == 'lane one\n'


@pytest.mark.parametrize('pattern', ['../escaped/{lane}', '/private/tmp/escaped/{lane}', '.crew/shared', '.crew/link/{lane}', '.crew/unowned/{lane}'])
def test_unsafe_paths_refuse_without_damage(mutable, pattern):
    project = mutable[1]; outside = project.parent / 'outside'; outside.mkdir()
    (project / '.crew').mkdir(); (project / '.crew/link').symlink_to(outside, target_is_directory=True)
    occupied = project / '.crew/unowned/lane0'; occupied.mkdir(parents=True)
    (occupied / 'keep').write_text('keep')
    runtime, adapter = run(mutable, worktree_pattern=pattern)
    with pytest.raises(ValueError): first(runtime, adapter)
    assert adapter.cwds == [] and (occupied / 'keep').read_text() == 'keep'
    assert not list(outside.iterdir())
    assert git(project, 'worktree', 'list', '--porcelain').count('worktree ') == 1


def test_multiple_repositories_first_cwd(mutable):
    repository(mutable[1] / 'second')
    runtime, adapter = run(mutable, repos=['.', 'second'], base_branch='main')
    _, state = first(runtime, adapter); lane = state['lanes'][0]
    assert len(lane['worktrees']) == 2 and adapter.cwds == [lane['worktrees'][0]['path']]
    for record in lane['worktrees']:
        assert Path(record['path'], 'source.txt').read_text() == 'original\n'
        assert git(record['path'], 'rev-parse', '--path-format=absolute', '--git-common-dir') == record['repository_id']


@pytest.mark.parametrize('repos', [[], ['.', '.'], ['missing'], ['../outside'], ['second']])
def test_invalid_repository_configuration(mutable, repos):
    runtime, adapter = run(mutable, repos=repos)
    with pytest.raises(ValueError): first(runtime, adapter)
    assert adapter.cwds == []


def test_readonly_non_git_compatibility(tmp_path, monkeypatch):
    root = _global_config(tmp_path / 'global'); project = tmp_path / 'project'; project.mkdir()
    monkeypatch.setenv('CREW_ROLE', 'orchestrator')
    runtime = Runtime(create_run(root, project, 'run', 'readonly', ['A']))
    adapter = FakeHerdr(); runtime.start(herdr=adapter); runtime.tick(herdr=adapter, resource_check=ready)
    assert next(iter(runtime.store.read_state()['tasks'].values()))['status'] == 'running'
    assert not (project / '.crew/worktrees').exists()


def test_explicit_retry_after_uncertain_create_is_durable_and_reuses_exact(mutable, monkeypatch):
    import crew.worktrees as worktrees
    runtime, adapter = run(mutable)
    original = worktrees.git; fail = [True]
    def interrupted(repository, *args):
        if args[:2] == ('worktree', 'add'):
            record = runtime.store.read_state()['lanes'][0]['worktrees'][0]
            assert record['status'] == 'creating'
            assert args[-2] == record['path']
            if fail.pop() if fail else False:
                raise OSError('simulated interruption before Git')
        return original(repository, *args)
    monkeypatch.setattr(worktrees, 'git', interrupted)
    with pytest.raises(ValueError, match='uncertain'): first(runtime, adapter)
    state = runtime.store.read_state(); task_id = next(iter(state['tasks']))
    assert state['lanes'][0]['worktrees'][0]['status'] == 'uncertain'
    with pytest.raises(ValueError, match='explicit'): first(runtime, adapter)
    assert adapter.cwds == []
    runtime.dispatch(task_id, herdr=adapter, resource_check=ready)
    state = runtime.store.read_state(); record = state['lanes'][0]['worktrees'][0]
    assert record['status'] == 'ready'
    before = git(mutable[1], 'worktree', 'list', '--porcelain')
    task_before = state['tasks'][task_id]
    runtime.dispatch(task_id, herdr=adapter, resource_check=ready)
    assert runtime.store.read_state()['tasks'][task_id] == task_before
    assert git(mutable[1], 'worktree', 'list', '--porcelain') == before
    assert adapter.cwds == [record['path']]


def test_command_error_after_creation_reconciles_real_registration(mutable, monkeypatch):
    import crew.worktrees as worktrees
    runtime, adapter = run(mutable)
    original = worktrees.git
    def interrupted(repository, *args):
        result = original(repository, *args)
        if args[:2] == ('worktree', 'add'):
            raise OSError('simulated lost result after Git')
        return result
    monkeypatch.setattr(worktrees, 'git', interrupted)
    _, state = first(runtime, adapter)
    assert state['lanes'][0]['worktrees'][0]['status'] == 'ready'
    assert len(adapter.cwds) == 1


@pytest.mark.parametrize('damage', ['branch', 'registration', 'repository', 'symlink', 'unowned'])
def test_retry_rejects_wrong_owned_identity_without_damage(mutable, monkeypatch, damage):
    import crew.worktrees as worktrees
    runtime, adapter = run(mutable)
    low = lambda *a, **k: type('Low', (), {'ready': False})()
    runtime.tick(herdr=adapter, resource_check=low)
    state = runtime.store.read_state(); task_id = next(iter(state['tasks']))
    record = state['lanes'][0]['worktrees'][0]; path = Path(record['path'])
    if damage == 'branch':
        git(path, 'switch', '-c', 'unowned')
    elif damage == 'registration':
        record['git_dir_identity'][1] += 1
        runtime.store.write_state(state)
    elif damage == 'repository':
        other = repository(mutable[1] / 'other')
        record['repository'] = str(other)
        runtime.store.write_state(state)
    elif damage == 'symlink':
        moved = path.with_name('moved'); path.rename(moved); path.symlink_to(moved, target_is_directory=True)
    else:
        # Replace owned location with legitimate, but unowned, foreign registration.
        git(mutable[1], 'worktree', 'move', str(path), str(path.with_name('retained')))
        other = repository(mutable[1] / 'other')
        git(other, 'worktree', 'add', '--detach', str(path), 'HEAD')
    before = (path / 'source.txt').read_bytes()
    with pytest.raises(ValueError): runtime.dispatch(task_id, herdr=adapter, resource_check=ready)
    assert (path / 'source.txt').read_bytes() == before and adapter.cwds == []


def test_preexisting_registered_worktree_is_not_adopted(mutable):
    project = mutable[1]; existing = project / '.crew/preexisting/lane0'
    git(project, 'worktree', 'add', '--detach', str(existing), 'HEAD')
    (existing / 'source.txt').write_text('dirty unowned\n')
    before = git(project, 'worktree', 'list', '--porcelain')
    runtime, adapter = run(mutable, worktree_pattern='.crew/preexisting/{lane}')
    with pytest.raises(ValueError, match='unowned'): first(runtime, adapter)
    assert git(project, 'worktree', 'list', '--porcelain') == before
    assert (existing / 'source.txt').read_text() == 'dirty unowned\n'


def test_explicit_retry_reconciles_interrupted_ready_write(mutable):
    runtime, adapter = run(mutable)
    low = lambda *a, **k: type('Low', (), {'ready': False})()
    runtime.tick(herdr=adapter, resource_check=low)
    state = runtime.store.read_state(); task_id = next(iter(state['tasks']))
    record = state['lanes'][0]['worktrees'][0]; record['status'] = 'creating'
    for key in ('git_dir', 'git_dir_identity', 'path_identity'): record.pop(key)
    runtime.store.write_state(state)
    before = git(mutable[1], 'worktree', 'list', '--porcelain')
    with pytest.raises(ValueError, match='explicit'): first(runtime, adapter)
    runtime.dispatch(task_id, herdr=adapter, resource_check=ready)
    assert git(mutable[1], 'worktree', 'list', '--porcelain') == before
    assert runtime.store.read_state()['lanes'][0]['worktrees'][0]['status'] == 'ready'


def test_worktree_gitfile_symlink_refuses(mutable):
    runtime, adapter = run(mutable)
    low = lambda *a, **k: type('Low', (), {'ready': False})()
    runtime.tick(herdr=adapter, resource_check=low)
    state = runtime.store.read_state(); task_id = next(iter(state['tasks']))
    path = Path(state['lanes'][0]['worktrees'][0]['path'])
    gitfile = path / '.git'; original = path / 'gitfile'; gitfile.rename(original); gitfile.symlink_to(original)
    with pytest.raises(ValueError, match='symlink'): runtime.dispatch(task_id, herdr=adapter, resource_check=ready)
    assert adapter.cwds == [] and original.exists()


@pytest.mark.parametrize('field', [
    '{run[0]}', '{run.__class__}', '{run[0].__class__}',
    '{run:>{lane[0]}}', '{run:>{lane.__class__}}',
    '{run:{lane:{repo[0]}}}', '{unknown}', '{}', '{run', '{run:invalid}',
])
def test_unsupported_pattern_fields_refuse_before_creation_or_launch(mutable, field):
    project = mutable[1]
    before_trees = git(project, 'worktree', 'list', '--porcelain')
    before_refs = git(project, 'for-each-ref', '--format=%(refname)')
    runtime, adapter = run(mutable, worktree_pattern=f'.crew/worktrees/{field}/{{lane}}/{{repo}}')
    with pytest.raises(ValueError, match='unsupported worktree_pattern'):
        first(runtime, adapter)
    assert adapter.cwds == []
    assert git(project, 'worktree', 'list', '--porcelain') == before_trees
    assert git(project, 'for-each-ref', '--format=%(refname)') == before_refs
    assert not (project / '.crew/worktrees').exists()
    assert all('worktrees' not in lane for lane in runtime.store.read_state()['lanes'])


@pytest.mark.parametrize(('pattern', 'relative'), [
    ('.crew/worktrees/{run}/{lane}/{repo}', '.crew/worktrees/run1/lane0/repo0'),
    ('.crew/{{literal}}/{run!s:>6}/{lane}/{repo}', '.crew/{literal}/  run1/lane0/repo0'),
])
def test_supported_pattern_formatting_preserved(mutable, pattern, relative):
    runtime, adapter = run(mutable, worktree_pattern=pattern)
    _, state = first(runtime, adapter)
    assert state['lanes'][0]['launch_cwd'] == str(mutable[1] / relative)
    assert adapter.cwds == [str(mutable[1] / relative)]
