"""Run management and conservative orchestration around the accepted core modules."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import sys
import stat
import tempfile
from uuid import uuid4

import yaml
from crew.config import CrewConfig, Team, load_config, safe_child, _validate_references, decode_yaml
from crew import control
from crew.dispatch import dispatch as dispatch_task
from crew.flow import next_tasks
from crew.flow_state import persist_next_tasks
from crew.herdr import Herdr
from crew.launch import LaunchResult, launch
from crew.resources import check_resources
from crew.state import StateStore
from crew.tick import tick as tick_task
from crew.worktrees import prepare_lane, cleanup_worktrees


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('expected nonempty ASCII identifier (letters, digits, underscore, hyphen)')
    return value


def require_lead():
    if os.environ.get('CREW_ROLE', 'lead') not in ('lead', 'orchestrator'):
        raise PermissionError('lead or orchestrator role required')


def require_orchestrator():
    if os.environ.get('CREW_ROLE') != 'orchestrator':
        raise PermissionError('CREW_ROLE=orchestrator required')


def canonical(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def atomic_yaml(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.crew-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as file:
            yaml.safe_dump(value, file, sort_keys=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def project_config(global_dir, project):
    project = Path(project)
    path = safe_child(project, '.crew/project.yaml')
    return load_config(global_dir, project_root=project,
                       project_file=path if path.exists() else None)


def init_project(global_dir, project):
    require_lead()
    project = Path(project).resolve()
    config = load_config(global_dir)
    target = safe_child(project, '.crew')
    target.mkdir(exist_ok=True)
    file = safe_child(project, '.crew/project.yaml')
    if not file.exists():
        # O_EXCL prevents overwriting a concurrent user's initialization.
        with file.open('x') as stream:
            yaml.safe_dump({}, stream)
    copied = []
    for source in sorted(Path(global_dir).rglob('*')):
        if source.is_symlink():
            raise ValueError('symlink in defaults tree')
        if not source.is_file() or source.name.startswith('.'):
            continue
        destination = safe_child(target, source.relative_to(global_dir))
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open('xb') as output:
                output.write(source.read_bytes())
            copied.append(str(destination.relative_to(target)))
        except FileExistsError:
            pass
    return {'project': str(project), 'copied': copied, 'status': 'initialized'}


def create_run(global_dir, project, run_id, flow, items, *, groups=1):
    require_lead()
    identifier(run_id); identifier(flow)
    if not items or len(set(items)) != len(items):
        raise ValueError('nonempty unique items required')
    for item in items:
        identifier(item)
    if type(groups) is not int or groups < 1 or groups > len(items):
        raise ValueError('groups must be between 1 and item count')
    project = Path(project).resolve()
    config = project_config(global_dir, project)
    if flow not in config.flows:
        raise ValueError('unknown flow')
    preview = next_tasks(config, flow, {'tasks': {}}, {}, item_id=items[0])
    if preview.status == 'blocked':
        raise ValueError(preview.reason)
    root = safe_child(project, '.crew/runs')
    path = safe_child(project, f'.crew/runs/{run_id}')
    frozen = config.model_dump(by_alias=True)
    root.mkdir(parents=True, exist_ok=True)
    # A private staging directory becomes visible only after initialization.
    temp = Path(tempfile.mkdtemp(prefix='.new-', dir=root))
    try:
        atomic_yaml(temp / 'run.yaml', frozen)
        lanes = [{'id': f'lane{index}', 'items': items[index::groups],
                  'files': [f'tickets/{item}' for item in items[index::groups]]}
                 for index in range(groups)]
        StateStore(temp).write_state({'run_id': run_id, 'flow': flow, 'items': list(items),
            'lanes': lanes, 'project': str(project), 'config_digest': digest(frozen),
            'tasks': {}, 'decisions': {}, 'owned_panes': []})
        if path.exists():
            raise ValueError('run already exists; choose another run ID')
        # rename refuses replacement of an initialized, nonempty run directory.
        os.rename(temp, path)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return path


def config_set(global_dir, project, assignment, *, scope='project', run_id=None):
    require_lead()
    key, separator, raw = assignment.partition('=')
    if not separator or not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', part) for part in key.split('.')):
        raise ValueError('use dot.path=YAML-value')
    value = decode_yaml(raw)
    project = Path(project).resolve()
    if scope == 'run':
        runtime = Runtime(safe_child(project, f'.crew/runs/{identifier(run_id)}'))
        with runtime.lock():
            state = runtime.store.read_state()
            if state.get('start_requested') or state.get('workspace') or state['tasks'] or state['decisions']:
                raise ValueError('run config is frozen after start; create another run')
            data = runtime.config.model_dump(by_alias=True)
            _set(data, key, value)
            validated = CrewConfig.model_validate(data)
            _validate_references(validated)
            data = validated.model_dump(by_alias=True)
            atomic_yaml(runtime.path / 'run.yaml', data)
            runtime.store.update_state(lambda s: {**s, 'config_digest': digest(data)})
            return {'scope': scope, 'path': str(runtime.path / 'run.yaml'), 'status': 'updated'}
    if scope not in ('project', 'global'):
        raise ValueError('unknown config scope')
    project_file = safe_child(project, '.crew/project.yaml')
    if scope == 'project':
        path = project_file
        data = decode_yaml(path.read_text()) if path.exists() else {}
        data = data or {}
        _set(data, key, value)
        # Validate proposed override using existing loader, before replacing user file.
        with tempfile.TemporaryDirectory() as scratch:
            candidate = Path(scratch) / 'project.yaml'
            atomic_yaml(candidate, data)
            # Validate against the actual project split files, not the scratch parent.
            load_config(global_dir, project_root=project, project_file=candidate)
    else:
        # Sections have their own files in the established global loader contract.
        parts = key.split('.')
        if parts[0] in ('roles', 'teams', 'axis'):
            if len(parts) < 2:
                raise ValueError('set a named section entry')
            path = Path(global_dir) / (parts.pop(0) + '.yaml')
            key = '.'.join(parts)
        elif parts[0] == 'flows':
            if len(parts) < 3:
                raise ValueError('set flows.<name>.<field>')
            path = safe_child(Path(global_dir), f'flows/{identifier(parts[1])}.yaml')
            key = '.'.join(parts[2:])
        else:
            path = Path(global_dir) / 'config.yaml'
        data = decode_yaml(path.read_text()) if path.exists() else {}
        _set(data, key, value)
        with tempfile.TemporaryDirectory() as scratch:
            copied = Path(scratch) / 'global'
            shutil.copytree(global_dir, copied, symlinks=False)
            atomic_yaml(copied / path.relative_to(global_dir), data)
            load_config(copied)
    atomic_yaml(path, data)
    return {'scope': scope, 'path': str(path), 'status': 'updated'}


def _set(data, key, value):
    parts = key.split('.')
    for part in parts[:-1]:
        data = data.setdefault(part, {})
        if not isinstance(data, dict):
            raise ValueError('dot path crosses a non-object value')
    data[parts[-1]] = value


class GuardStore(StateStore):
    """Let accepted tick commit only against exact state and verified source bytes."""
    def __init__(self, original, expected, verify):
        super().__init__(original.run_dir)
        self.expected = canonical(expected)
        self.verify = verify

    def update_state(self, update):
        with self._lock():
            state = self._read_unlocked()
            if canonical(state) != self.expected:
                raise ValueError('authoritative snapshot changed during reconciliation')
            self.verify()
            return self._write_unlocked(update(state))


class EvidenceStore(StateStore):
    """Validate external evidence at bridge lock entry and immediately before write."""
    def __init__(self, original, verify):
        super().__init__(original.run_dir)
        self.verify = verify

    @contextmanager
    def _lock(self):
        with super()._lock():
            self.verify()
            yield

    def _write_unlocked(self, state):
        self.verify()
        return super()._write_unlocked(state)


class RoleAdapter:
    """Add role environment without changing accepted launch contract."""
    def __init__(self, adapter, env):
        self.adapter = adapter
        self.env = env

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    def create_tab(self, *args, env=None, **kwargs):
        return self.adapter.create_tab(*args, env={**(env or {}), **self.env}, **kwargs)


class Runtime:
    def __init__(self, run_dir):
        self.path = Path(run_dir).absolute()
        if self.path.is_symlink() or any(parent.is_symlink() for parent in self.path.parents):
            raise ValueError('symlink run paths are forbidden')
        if not self.path.is_dir():
            raise ValueError('run not found; use crew run new')
        self.store = StateStore(self.path)
        self.config = CrewConfig.model_validate(decode_yaml(safe_child(self.path, 'run.yaml').read_text()))
        _validate_references(self.config)
        self.check_config()

    def check_config(self):
        state = self.store.read_state()
        on_disk = CrewConfig.model_validate(decode_yaml(safe_child(self.path, 'run.yaml').read_text()))
        if (digest(on_disk.model_dump(by_alias=True)) != state.get('config_digest')
                or digest(self.config.model_dump(by_alias=True)) != state.get('config_digest')):
            raise ValueError('frozen run configuration changed; reconciliation required')
        return state

    @contextmanager
    def lock(self):
        fd = os.open(self.path / '.runtime.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def start(self, *, herdr=None, controller='legacy'):
        require_lead()
        if controller not in ('legacy', 'script'):
            raise ValueError('unknown controller')
        with self.lock():
            state = self.check_config()
            if state.get('start_uncertain'):
                raise ValueError('start uncertain; inspect authoritative state and workspace before retry: '
                                 + str(state.get('start_error', 'unknown start error')))
            if state.get('workspace'):
                return state
            if state.get('start_intent'):
                raise ValueError('start already requested; inspect authoritative state and workspace before retry')
            if state.get('stop_requested') or state.get('close_requested'):
                raise ValueError('stopped or closing run cannot start')
            adapter = herdr or Herdr(self.config.herdr_session)
            if (controller == 'legacy' and os.environ.get('CREW_ROLE') != 'orchestrator'
                    and 'orchestrator' not in self.config.roles):
                raise ValueError('configure orchestrator role before crew run start')
            self.store.update_state(lambda s: {**s, 'start_requested': True, 'start_intent': uuid4().hex,
                                               'controller': controller})
            try:
                result = adapter.command('workspace', 'create', '--cwd', state['project'], '--label', state['run_id'], '--no-focus')
                workspace = result.get('workspace', {}).get('workspace_id')
                if not isinstance(workspace, str) or not workspace:
                    raise ValueError('workspace identity unavailable')
                self.store.update_state(lambda s: {**s, 'workspace': workspace})
                if controller == 'script':
                    self._start_script_runner(adapter, workspace)
                elif os.environ.get('CREW_ROLE') != 'orchestrator':
                    self._launch_orchestrator(adapter, state, workspace)
                return self.store.read_state()
            except Exception as error:
                self._retain_start_uncertainty(error)
                raise ValueError('start uncertain; inspect authoritative state and workspace before retry') from None

    def _launch_orchestrator(self, adapter, state, workspace):
        """Launch the configured orchestrator agent through the accepted launch contract."""
        role = self.config.roles['orchestrator']
        prompt = self.role_prompt(role, state['project'])
        prompt += f'\nRun: {self.path}\nUse ' + shlex.join(['crew', '--project', state['project'], 'tick', state['run_id']]) + '. Never answer dialogs.\n'
        orch_intent = uuid4().hex
        self.store.update_state(lambda s: {**s, 'orchestrator': {
            'launch_intent': orch_intent, 'mode': role.mode, 'status': 'launching'}})
        launched = launch(RoleAdapter(adapter, {'CREW_ROLE': 'orchestrator'}), workspace=workspace, name=f'crew-{state["run_id"]}-orchestrator',
                          cwd=state['project'], role=role, prompt=prompt, axis=self.config.axis)
        def record_orchestrator(s):
            s['orchestrator'].update(asdict(launched))
            if launched.pane_id:
                s['owned_panes'] = sorted(set(s['owned_panes']) | {launched.pane_id})
            return s
        self.store.update_state(record_orchestrator)

    def _retain_start_uncertainty(self, error):
        """Record an uncertain start without overwriting proven child progress."""
        partial = getattr(error, 'launch_result', None)
        def retain_uncertain(s):
            s['start_error'] = str(error)
            record = s.get('orchestrator') or s.get('runner')
            if partial is not None and partial.pane_id:
                s['owned_panes'] = sorted(set(s['owned_panes']) | {partial.pane_id})
            # A child that already left `launching` proved progress. Keep
            # its evidence; do not overwrite it with the parent's uncertainty.
            if record is not None and record.get('status') not in ('launching', None):
                return s
            s['start_uncertain'] = True
            if partial is not None and record is not None:
                record.update(asdict(partial))
            return s
        self.store.update_state(retain_uncertain)

    def _start_script_runner(self, adapter, workspace):
        """Launch the ordinary script controller once as an unfocused pane command."""
        state = self.store.read_state()
        run_id = state['run_id']
        module_root = str(Path(__file__).resolve().parent.parent)
        command = shlex.join(['env', 'CREW_ROLE=orchestrator', 'PYTHONPATH=' + module_root,
                              sys.executable, '-m', 'crew.cli', '--project', state['project'], 'advance', run_id])
        try:
            tab_id, pane_id = adapter.create_tab(workspace, state['project'], f'crew-{run_id}-runner')
        except Exception as error:
            partial = LaunchResult(getattr(error, 'tab_id', None), getattr(error, 'pane_id', None), 'uncertain')
            error.launch_result = partial
            raise
        runner_intent = uuid4().hex
        def record(s):
            s.setdefault('runner', {})
            s['runner'].update({'launch_intent': runner_intent, 'mode': 'script', 'controller': 'script',
                                'status': 'launching', 'tab_id': tab_id, 'pane_id': pane_id})
            s['owned_panes'] = sorted(set(s['owned_panes']) | {pane_id})
            return s
        self.store.update_state(record)
        adapter.run_pane(pane_id, command)
        # A fast child can record progress before this acknowledgement. Only
        # acknowledge the same launch intent while it is still launching.
        def ack(s):
            runner = s.get('runner') or {}
            if runner.get('launch_intent') == runner_intent and runner.get('status') == 'launching':
                runner['status'] = 'started'
                s['runner'] = runner
            return s
        self.store.update_state(ack)

    def role_prompt(self, role, project):
        if not role.prompt:
            return 'Complete assigned task. Write only assigned output artifacts.'
        source = Path(role.prompt)
        if source.is_absolute():
            raise ValueError('prompt must be relative to project .crew')
        path = safe_child(Path(project) / '.crew', source)
        if not path.is_file():
            raise ValueError(f'prompt missing: {role.prompt}; run crew project init')
        return path.read_text()

    def request_control(self, action, *, team=None, keep_worktrees=False):
        require_lead()
        if action not in ('pause', 'resume', 'stop', 'close'):
            raise ValueError('unknown control')
        if team is not None and team not in self.config.teams:
            raise ValueError('unknown team')
        with self.lock():
            def update(state):
                intents = state.setdefault('control_requests', {})
                intents[team or '*'] = {'action': action, 'keep_worktrees': bool(keep_worktrees)}
                if team is None:
                    if action == 'resume':
                        if state.get('stop_requested') or state.get('close_requested'):
                            raise ValueError('stopped or closing run cannot resume')
                        state.pop('pause_requested', None)
                    else:
                        state[action + '_requested'] = True
                return state
            return self.store.update_state(update)

    def decide(self, decision_id, choice):
        if os.environ.get('CREW_ROLE') != 'lead':
            raise PermissionError('CREW_ROLE=lead required')
        with self.lock():
            self.check_config()
            def update(state):
                decision = state['decisions'].get(decision_id)
                if not decision:
                    raise ValueError('unknown exact decision')
                if decision.get('kind') == 'question':
                    if choice not in decision['options']:
                        raise ValueError('choice must match a configured option')
                    if decision.get('choice') == choice:
                        return state
                    if decision['status'] != 'pending':
                        raise ValueError('decision already resolved')
                    decision.update(status='approved', choice=choice)
                    return state
                if choice not in ('approved', 'rejected'):
                    raise ValueError('choice must be approved or rejected')
                if decision['status'] == choice:
                    return state
                if decision['status'] != 'pending':
                    raise ValueError('decision already resolved')
                # Recompute reachability instead of trusting arbitrary inserted IDs.
                item = decision_id.split(':', 1)[0]
                if item not in state['items']:
                    raise ValueError('unknown decision item')
                evidence = self.evidence(state, item)
                result = next_tasks(self.config, state['flow'], state, evidence, item_id=item)
                if result.decision_id != decision_id or result.status != 'user_decision':
                    raise ValueError('decision is not current exact step/round')
                decision['status'] = choice
                return state
            return self.store.update_state(update)

    def ask(self, question, options):
        require_orchestrator()
        if not question.strip() or len(options) < 2 or len(set(options)) != len(options):
            raise ValueError('question and two distinct options required')
        with self.lock():
            state = self.check_config()
            if state.get('controller') == 'script':
                raise ValueError('script controller owns this run; user questions are not available')
            decision_id = 'ask-' + digest([question, options])[:16]
            def update(current):
                current['decisions'].setdefault(decision_id, {'kind': 'question', 'status': 'pending',
                                                             'question': question, 'options': options})
                return current
            return self.store.update_state(update)['decisions'][decision_id]

    def artifact_relative(self, task_id, task):
        parts = task_id.split(':')
        if len(parts) != 5:
            raise ValueError('unknown deterministic task identity')
        item, flow, step, round_number, role_index = parts
        identifier(item); identifier(flow)
        if not re.fullmatch(r's[0-9]+', step) or not re.fullmatch(r'r[0-9]+', round_number) or not re.fullmatch(r'role[0-9]+', role_index):
            raise ValueError('malformed deterministic task identity')
        team = self.config.teams[task['team']]
        output = team.handoffs[task['role']].out.format(ticket=item, item=item, round=task['round'], step=task['step'], role=task['role'])
        if not output or Path(output).is_absolute() or '..' in Path(output).parts:
            raise ValueError('handoff output must be a relative path')
        return str(Path('tickets') / item / flow / step / round_number / role_index / output)

    def read_artifact(self, state, task_id):
        """Open components relative to no-follow directory FDs; limit input to 1 MiB."""
        task = state['tasks'][task_id]
        relative = self.artifact_relative(task_id, task)
        if task.get('artifact') != relative:
            raise ValueError('artifact does not match task ownership')
        if sum(other.get('artifact') == relative for other in state['tasks'].values()) != 1:
            raise ValueError('artifact has multiple owners')
        timestamp = task.get('launch_started_at_ns')
        if type(timestamp) is not int or timestamp <= 0 or not task.get('launch_intent'):
            raise ValueError('artifact launch freshness unavailable')
        safe_child(self.path, relative)
        directory = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = Path(relative).parts
            for part in parts[:-1]:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory); directory = following
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                before = os.fstat(fd)
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or before.st_size > 1024 * 1024 or before.st_mtime_ns < timestamp):
                    raise ValueError('artifact is not regular, private, bounded and fresh')
                with os.fdopen(fd, 'rb', closefd=False) as stream:
                    raw = stream.read(1024 * 1024 + 1)
                after = os.fstat(fd)
                current = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
                stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                if stamp(before) != stamp(after) or stamp(after) != stamp(current) or len(raw) > 1024 * 1024:
                    raise ValueError('artifact changed during read')
                text = raw.decode('utf-8')
                marker = self.config.teams[task['team']].done_marker
                lines = text.splitlines()
                if marker not in lines or not any(line.strip() and line != marker for line in lines):
                    raise ValueError('artifact completion marker/body unavailable')
                return text, [*stamp(after), hashlib.sha256(raw).hexdigest()]
            finally:
                os.close(fd)
        finally:
            os.close(directory)

    def evidence(self, state, item):
        evidence = {}
        prefix = f'{item}:{state["flow"]}:'
        for task_id, task in state['tasks'].items():
            if task_id.startswith(prefix) and task['status'] == 'done':
                text, fingerprint = self.read_artifact(state, task_id)
                if task.get('artifact_fingerprint') != fingerprint:
                    raise ValueError('completed artifact changed; reconciliation required')
                evidence[task_id] = text
        return evidence

    def reconcile(self, adapter, task_id):
        state = self.store.read_state()
        task = state['tasks'][task_id]
        if task['status'] not in ('running', 'blocked') or task.get('closed'):
            return
        try:
            if task.get('mode') == 'oneshot':
                self.reconcile_oneshot(task_id)
                return
            # Missing output while agent works is ordinary waiting; identity still gets checked.
            try:
                text, fingerprint = self.read_artifact(state, task_id)
            except FileNotFoundError:
                text, fingerprint = '', None
            if fingerprint is None:
                team = deepcopy(self.config.teams[task['team']])
                with tempfile.TemporaryDirectory() as temporary:
                    team.handoffs[task['role']].out = str(Path(temporary) / 'missing')
                    tick_task(GuardStore(self.store, state, lambda: None), task_id, herdr=adapter, team=team)
                return
            with tempfile.TemporaryDirectory() as temporary:
                staged = Path(temporary) / 'verified.md'
                staged.write_text(text)
                os.utime(staged, ns=(task['launch_started_at_ns'], task['launch_started_at_ns']))
                team = deepcopy(self.config.teams[task['team']])
                team.handoffs[task['role']].out = str(staged)
                def verify():
                    if self.read_artifact(state, task_id)[1] != fingerprint:
                        raise ValueError('artifact changed during reconciliation')
                result = tick_task(GuardStore(self.store, state, verify), task_id, herdr=adapter, team=team)
                if result['status'] == 'done':
                    def record(current):
                        if current['tasks'][task_id] != result:
                            raise ValueError('task changed before recording evidence')
                        verify()
                        current['tasks'][task_id]['artifact_fingerprint'] = fingerprint
                        return current
                    self.store.update_state(record)
        except (OSError, UnicodeError, ValueError, KeyError) as error:
            def blocked(current):
                if current['tasks'].get(task_id) == task:
                    current['tasks'][task_id].update(status='blocked', reconciliation_needed=True,
                                                    reconciliation_error=str(error))
                return current
            self.store.update_state(blocked)

    def apply_controls(self, adapter):
        state = self.store.read_state()
        for task_id, task in state['tasks'].items():
            intent = state.get('control_requests', {}).get(task['team'], state.get('control_requests', {}).get('*'))
            if not intent:
                continue
            action = intent['action']
            if action == 'close':
                if not task.get('launch_intent'):
                    control.stop(self.store, task_id)
                control.close(self.store, task_id, herdr=adapter,
                    read_receipt=lambda key=task_id: self.read_exit_receipt(self.store.read_state(), key))
            else:
                getattr(control, action)(self.store, task_id)

    def read_exit_receipt(self, state, task_id):
        task = state['tasks'][task_id]
        relative = str(Path(task['artifact']).parent / ('.exit-' + task['launch_intent'] + '.json'))
        path = safe_child(self.path, relative)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 4096 or info.st_mtime_ns < task['launch_started_at_ns']:
                raise ValueError('exit receipt is not private, regular, bounded and fresh')
            with os.fdopen(fd, 'r', closefd=False) as stream:
                receipt = json.load(stream)
            if (set(receipt) != {'launch_intent', 'exit_code'} or receipt['launch_intent'] != task['launch_intent']
                    or type(receipt['exit_code']) is not int or not 0 <= receipt['exit_code'] <= 255):
                raise ValueError('exit receipt does not match launch')
            return receipt
        finally:
            os.close(fd)

    def reconcile_oneshot(self, task_id):
        state = self.store.read_state()
        task = state['tasks'][task_id]
        if task['status'] not in ('running', 'blocked'):
            return
        # A matching receipt is completion evidence even when launch reported an
        # error. Attempt reconciliation each tick; invalid or missing evidence
        # stays blocked below and never completes.
        if (not task.get('launch_intent') or not task.get('pane_id') or not task.get('tab_id')
                or state.get('owned_panes', []).count(task['pane_id']) != 1
                or any(key != task_id and other.get('pane_id') == task['pane_id'] for key, other in state['tasks'].items())):
            raise ValueError('oneshot ownership is unavailable')
        try:
            receipt = self.read_exit_receipt(state, task_id)
        except FileNotFoundError:
            return
        text, fingerprint = self.read_artifact(state, task_id) if receipt['exit_code'] == 0 else ('', None)
        def verify():
            if self.read_exit_receipt(state, task_id) != receipt:
                raise ValueError('exit receipt changed')
            if fingerprint is not None and self.read_artifact(state, task_id)[1] != fingerprint:
                raise ValueError('artifact changed')
        def complete(current):
            record = current['tasks'][task_id]
            record['status'] = 'done' if receipt['exit_code'] == 0 else 'failed'
            record['exit_code'] = receipt['exit_code']
            record.pop('reconciliation_needed', None)
            if fingerprint is not None:
                record['artifact_fingerprint'] = fingerprint
            return current
        GuardStore(self.store, state, verify).update_state(complete)

    def dispatch(self, task_id, *, herdr=None, resource_check=check_resources, controller=None):
        require_orchestrator()
        with self.lock():
            if self.store.read_state().get('controller') == 'script' and controller != 'script':
                raise ValueError('script controller owns this run; use crew advance')
            return self._dispatch(task_id, herdr or Herdr(self.config.herdr_session), resource_check, retry_worktree=True)

    def _dispatch(self, task_id, adapter, resource_check, *, retry_worktree=False):
        state = self.check_config()
        if not state.get('workspace'):
            raise ValueError('run has no verified workspace; use crew run start')
        task = state['tasks'][task_id]
        requests = state.get('control_requests', {})
        intent = requests.get(task['team'], requests.get('*', {}))
        if (state.get('pause_requested') or state.get('stop_requested') or state.get('close_requested')
                or intent.get('action') in ('pause', 'stop', 'close') or task.get('pause_requested') or task.get('stop_requested')):
            return task
        proposal_state = deepcopy(state)
        proposal_state['tasks'].pop(task_id)
        item = task_id.split(':')[0]
        if item not in state['items']:
            raise ValueError('task item is outside run')
        proposal = next_tasks(self.config, state['flow'], proposal_state, self.evidence(state, item), item_id=item)
        if proposal.status != 'ready' or proposal.tasks[0].id != task_id:
            raise ValueError('task is not the current reachable flow proposal')
        expected = proposal.tasks[0]
        if any(type(task.get(field)) is not type(getattr(expected, field)) or task.get(field) != getattr(expected, field)
               for field in ('team', 'role', 'step', 'round')):
            raise ValueError('task metadata conflicts with current proposal')
        role = deepcopy(self.config.roles[task['role']])
        team = self.config.teams[task['team']]
        role.axis = sorted(set(role.axis + team.axis))
        # External operation roles need a separate execution boundary, beyond planner approval.
        step = self.config.flows[state['flow']].steps[task['step']]
        if step.action or task['team'] in ('push', 'pr', 'merge', 'pull-request'):
            raise ValueError('external operation execution is unsupported; user decision alone cannot enforce tool permission')
        if any(t.get('launch_intent') and t['status'] not in ('done', 'failed', 'paused')
               and (not role.readonly or not self.config.roles[t['role']].readonly)
               for key, t in state['tasks'].items() if key != task_id):
            def serialized(current):
                item = current['tasks'][task_id]
                if item['status'] == 'queued' and 'launch_intent' not in item:
                    item['resource_wait'] = 'mutable dispatch serialized behind an active writer'
                return current
            return dict(self.store.update_state(serialized)['tasks'][task_id])
        cwd = task.get('launch_cwd', state['project'])
        lane = None
        if not role.readonly and task['status'] == 'queued' and not task.get('launch_intent'):
            lane = prepare_lane(self.store, self.config, item, retry=retry_worktree)
            cwd = lane['launch_cwd']
        artifact = self.artifact_relative(task_id, task)
        output = safe_child(self.path, artifact)
        prompt = self.role_prompt(role, state['project'])
        prompt += f'\nTask: {task_id}\nOutput: {output}\nCompletion marker (standalone): {team.done_marker}\n'
        handoff_in = team.handoffs[task['role']].in_.format(ticket=task_id.split(':')[0], item=task_id.split(':')[0], round=task['round'], step=task['step'], role=task['role'])
        input_path = safe_child(self.path, handoff_in)
        prior = [other['artifact'] for key, other in state['tasks'].items() if key.startswith(task_id.split(':')[0] + ':') and other['status'] == 'done' and other.get('artifact')]
        prompt += f'Input: {input_path}\nVerified prior artifacts: ' + ', '.join(str(self.path / value) for value in prior) + '\n'
        if lane is not None:
            prompt += 'Assigned source worktrees: ' + ', '.join(record['path'] for record in lane['worktrees']) + '\n'
            prompt += 'Edit source only inside assigned lane worktrees. Write your assigned artifact.\n'
        else:
            prompt += 'Write only your assigned artifact.\n'
        prompt += 'Do not change run state. Do not answer approval dialogs.\n'
        if role.axis:
            prompt += 'Use allowed AXI tools instead of raw CLI/MCP: ' + ', '.join(role.axis) + '.\n'
        output.parent.mkdir(parents=True, exist_ok=True)
        def assign(current):
            record = current['tasks'][task_id]
            if record.get('artifact', artifact) != artifact:
                raise ValueError('artifact ownership changed')
            record['artifact'] = artifact
            record['launch_cwd'] = cwd
            if lane is not None:
                record['worktree_lane'] = lane['id']
                record['worktrees'] = lane['worktrees']
            return current
        self.store.update_state(assign)
        def launch_bound(adapter, **kwargs):
            if role.mode != 'oneshot':
                return launch(RoleAdapter(adapter, {'CREW_ROLE': 'worker'}), **kwargs)
            reserved = self.store.read_state()['tasks'][task_id]
            receipt = self.path / Path(artifact).parent / ('.exit-' + reserved['launch_intent'] + '.json')
            class ReceiptAdapter(RoleAdapter):
                def run_pane(inner, pane, command):
                    # Use the assigned project interpreter; argv and paths are shell quoted.
                    writer = shlex.join([sys.executable, '-m', 'crew.receipt', str(receipt), reserved['launch_intent']])
                    module_root = str(Path(__file__).resolve().parent.parent)
                    wrapped = '(' + command + '); crew_exit_code=$?; ' + shlex.join(['env', 'PYTHONPATH=' + module_root]) + ' ' + writer + ' "$crew_exit_code"'
                    return inner.adapter.run_pane(pane, wrapped)
            return launch(ReceiptAdapter(adapter, {'CREW_ROLE': 'worker'}), **kwargs)
        return dispatch_task(self.store, task_id, herdr=adapter, workspace=state['workspace'],
            name='crew-' + digest([state['run_id'], task_id])[:20], role_name=task['role'],
            cwd=cwd, role=role, prompt=prompt, limits=self.config.resources,
            axis=self.config.axis, resource_check=resource_check, launch_role=launch_bound)

    def _plan_item(self, state, item):
        """Persist the next flow proposal for one item against verified evidence."""
        snapshot = self.check_config()
        try:
            evidence = self.evidence(snapshot, item)
            def verify():
                frozen = CrewConfig.model_validate(decode_yaml((self.path / 'run.yaml').read_text()))
                if digest(frozen.model_dump(by_alias=True)) != snapshot['config_digest']:
                    raise ValueError('frozen configuration changed during planning')
                if self.evidence(snapshot, item) != evidence:
                    raise ValueError('artifact changed during planning')
            result = persist_next_tasks(EvidenceStore(self.store, verify), self.config, state['flow'], evidence,
                                        item_id=item, expected_snapshot=snapshot)
            return result.status
        except (OSError, UnicodeError, ValueError, KeyError):
            return 'blocked'

    def tick(self, *, herdr=None, resource_check=check_resources, controller=None):
        require_orchestrator()
        with self.lock():
            state = self.check_config()
            if state.get('controller') == 'script' and controller != 'script':
                raise ValueError('script controller owns this run; use crew advance')
            adapter = herdr or Herdr(self.config.herdr_session)
            if not state.get('workspace'):
                raise ValueError('run has no verified workspace; use crew run start')
            adapter.owned_panes.update(state.get('owned_panes', []))
            self.apply_controls(adapter)
            for task_id in list(self.store.read_state()['tasks']):
                self.reconcile(adapter, task_id)
            flows = {}
            for item in state['items']:
                flows[item] = self._plan_item(state, item)
            state = self.store.read_state()
            for task_id, task in state['tasks'].items():
                if task['status'] == 'queued' and not task.get('launch_intent'):
                    self._dispatch(task_id, adapter, resource_check)
            # Reconcile first, then close newly completed resources in this same tick.
            self.apply_controls(adapter)
            cleanup = self.cleanup(adapter)
            return {'run_id': state['run_id'], 'flows': flows,
                    'tasks': self.store.read_state()['tasks'], 'cleanup': cleanup}


    def cleanup(self, adapter):
        """Report cooperative controls honestly; never signal an unproven active process."""
        state = self.store.read_state()
        closing = state.get('close_requested', False)
        if closing and state.get('orchestrator'):
            control.close(self.store, None, herdr=adapter)
        state = self.store.read_state()
        requests = state.get('control_requests', {})
        keep = any(r.get('keep_worktrees') for r in requests.values() if r.get('action') == 'close')
        trees = cleanup_worktrees(self.store, keep=keep) if closing else []
        state = self.store.read_state()
        active = [key for key, task in state['tasks'].items()
                  if (task.get('launch_intent') or task.get('pane_id')) and not task.get('closed')]
        orch = state.get('orchestrator', {})
        orch_report = {'pane_id': orch.get('pane_id'), 'closed': bool(orch.get('closed')),
                       'reason': orch.get('close_error', 'active or uncertain process retained')}
        retained = []
        if state.get('workspace'):
            retained.append({'kind': 'workspace', 'id': state['workspace'],
                             'reason': 'containing workspace and shell retained; no exact cleanup ownership'})
        for pane in state.get('owned_panes', []):
            retained.append({'kind': 'pane', 'id': pane, 'reason': 'active or uncertain process retained'})
        if state.get('start_uncertain'):
            retained.append({'kind': 'start', 'reason': 'start outcome uncertain; resources retained'})
        branches = [{'repository': record['repository'], 'branch': record['branch']}
                    for lane in state['lanes'] for record in lane.get('worktrees', [])
                    if record.get('status') != 'planned']
        settled = not active and not state.get('owned_panes') and not state.get('start_uncertain')
        if orch and not orch.get('closed'):
            settled = False
        report = {'active_tasks': active, 'orchestrator': orch_report, 'worktrees': trees,
                  'retained_branches': branches, 'retained_resources': retained,
                  'stopped': bool(state.get('stop_requested') or closing) and settled,
                  'closed': bool(closing) and settled and not retained and not branches
                            and all(t['status'] == 'removed' for t in trees)}
        self.store.update_state(lambda s: {**s, 'cleanup': report})
        return report
