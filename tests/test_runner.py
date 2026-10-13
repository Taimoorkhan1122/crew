"""Script controller runner: ownership, polling, controls, and honest exit."""
import fcntl
import math
import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest
import yaml
from test_config import _global_config, _write_yaml
from crew.resources import ResourceReport, QuotaAvailability


def api():
    from crew.runner import Runner, RunnerError, RunnerLimits
    return Runner, RunnerError, RunnerLimits


@pytest.fixture
def setup(tmp_path):
    root = _global_config(tmp_path / 'global')
    project = tmp_path / 'project'
    project.mkdir()
    return root, project


@pytest.fixture(autouse=True)
def lead_role(monkeypatch):
    monkeypatch.setenv('CREW_ROLE', 'lead')


def ready(*a, **k):
    return ResourceReport(8, .1, k['active_workers'], QuotaAvailability('available', 1000), True)


def make_run(setup, items=('A', 'B'), **kw):
    from crew.runtime import Runtime, create_run
    path = create_run(*setup, 'run1', 'readonly', list(items), **kw)
    return Runtime(path), path


class FakeHerdr:
    def __init__(self):
        self.owned_panes = set()
        self.agents = {}
        self.prompts = []
        self.closed = []
        self.pane_runs = []
        self.tabs = []

    def create_tab(self, workspace, cwd, label, **kw):
        pane = f'w1:p{len(self.agents) + 1}'
        self.owned_panes.add(pane)
        self.agents[pane] = {'pane_id': pane, 'agent_status': 'idle', 'agent_session': {
            'kind': 'id', 'source': 'integration:fake', 'agent': 'fake', 'value': pane}}
        self.tabs.append((workspace, cwd, label, kw.get('env', {})))
        return 'w1:t1', pane

    def start(self, name, kind, pane, args, **kw):
        return self.agents[pane]

    def prompt(self, pane, message, **kw):
        from crew.herdr import PromptOutcome
        self.prompts.append(message)
        return PromptOutcome('settled', self.agents[pane])

    def get_agent(self, pane):
        return self.agents[pane]

    def run_pane(self, pane, command):
        self.pane_runs.append((pane, command))

    def command(self, *args, **kw):
        if args[:2] == ('workspace', 'create'):
            return {'workspace': {'workspace_id': 'w1'}}
        if args[:2] == ('pane', 'close'):
            self.closed.append(args[2])
            return {}
        raise AssertionError(args)


def start_script(runtime, adapter):
    runtime.start(herdr=adapter, controller='script')
    return runtime.store.read_state()


def test_runner_limits_reject_nonfinite_and_zero():
    _, _, RunnerLimits = api()
    for bad in (0, -1, float('nan'), float('inf'), float('-inf')):
        with pytest.raises(ValueError):
            RunnerLimits(min_delay=bad, max_delay=2)
    with pytest.raises(ValueError):
        RunnerLimits(min_delay=5, max_delay=1)
    limits = RunnerLimits(min_delay=1, max_delay=10)
    assert (limits.min_delay, limits.max_delay) == (1, 10)


def test_runner_limits_enforce_practical_bounds():
    _, _, RunnerLimits = api()
    limits = RunnerLimits(min_delay=0.1, max_delay=60)
    assert (limits.min_delay, limits.max_delay) == (0.1, 60)
    for bad in (0.05, 0.099, 60.5, 1e9):
        with pytest.raises(ValueError):
            RunnerLimits(min_delay=bad, max_delay=60)
        with pytest.raises(ValueError):
            RunnerLimits(min_delay=0.1, max_delay=bad)


def test_runner_sleep_exception_records_failure(setup, monkeypatch):
    Runner, _, RunnerLimits = api()
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    start_script(runtime, FakeHerdr())

    def boom(_seconds):
        raise RuntimeError('sleep failed')

    runner = Runner(path, herdr=FakeHerdr(), resource_check=ready, sleep=boom,
                    limits=RunnerLimits(0.1, 0.2))
    summary, code = runner.run(max_iterations=5)
    assert code == 1
    assert summary['runner']['status'] == 'failed'
    assert 'sleep failed' in summary['runner']['reason']
    assert runtime.store.read_state()['runner']['status'] == 'failed'


def test_runner_refuses_run_without_script_controller(setup):
    Runner, RunnerError, RunnerLimits = api()
    runtime, path = make_run(setup)
    with pytest.raises(RunnerError, match='script-controlled'):
        Runner(path, limits=RunnerLimits(0.1, 0.2)).run(max_iterations=1)


def test_run_start_script_launches_runner_tab_without_orchestrator(setup, monkeypatch):
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    adapter = FakeHerdr()
    state = start_script(runtime, adapter)
    assert state['controller'] == 'script'
    assert state['start_intent']
    assert state['workspace'] == 'w1'
    assert state['runner']['controller'] == 'script'
    assert 'orchestrator' not in state
    assert adapter.pane_runs, 'runner command was not submitted'
    pane, command = adapter.pane_runs[-1]
    assert 'advance run1' in command and 'crew.cli' in command and '-m' in command
    assert sys.executable in command
    assert 'PYTHONPATH=' in command
    assert str(setup[1]) in command
    assert state['owned_panes'] == [pane]


def test_runner_start_keeps_fast_child_status_and_never_resends(setup, monkeypatch):
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)

    class Fast(FakeHerdr):
        def run_pane(self, pane, command):
            self.pane_runs.append((pane, command))
            runtime.store.update_state(
                lambda s: (s.setdefault('runner', {}).update(status='running', pid=4242) or s))

    adapter = Fast()
    runtime.start(herdr=adapter, controller='script')
    state = runtime.store.read_state()
    assert state['runner']['status'] == 'running' and state['runner']['pid'] == 4242
    assert len(adapter.pane_runs) == 1


def test_runner_start_keeps_child_failure_when_submission_raises(setup, monkeypatch):
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)

    class Fast(FakeHerdr):
        def run_pane(self, pane, command):
            self.pane_runs.append((pane, command))
            runtime.store.update_state(
                lambda s: (s.setdefault('runner', {}).update(status='failed', reason='child failed') or s))
            raise RuntimeError('submission uncertain')

    adapter = Fast()
    with pytest.raises(ValueError):
        runtime.start(herdr=adapter, controller='script')
    state = runtime.store.read_state()
    assert state['runner']['status'] == 'failed' and state['runner']['reason'] == 'child failed'
    assert 'start_uncertain' not in state
    assert len(adapter.pane_runs) == 1


def test_manual_tick_and_dispatch_refused_for_script_run(setup, monkeypatch):
    monkeypatch.setenv('CREW_ROLE', 'orchestrator')
    runtime, path = make_run(setup)
    adapter = FakeHerdr()
    start_script(runtime, adapter)
    with pytest.raises(ValueError, match='script controller'):
        runtime.tick(herdr=adapter, resource_check=ready)
    with pytest.raises(ValueError, match='script controller'):
        runtime.dispatch('A:readonly:s0:r0:role0', herdr=adapter, resource_check=ready)
    assert adapter.prompts == []


def test_second_runner_cannot_acquire_run_lock(setup, monkeypatch):
    Runner, RunnerError, RunnerLimits = api()
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    start_script(runtime, FakeHerdr())
    fd = os.open(path / '.runner.lock', os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(RunnerError, match='already owns'):
            Runner(path, herdr=FakeHerdr(), resource_check=ready,
                   sleep=lambda s: None, limits=RunnerLimits(0.1, 0.2)).run(max_iterations=1)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_runner_dispatches_waits_then_completes(setup, monkeypatch):
    Runner, _, RunnerLimits = api()
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    adapter = FakeHerdr()
    start_script(runtime, adapter)
    runner = Runner(path, herdr=adapter, resource_check=ready,
                    sleep=lambda s: None, limits=RunnerLimits(0.5, 1))
    summary, code = runner.run(max_iterations=2)
    assert code == 124 and summary['runner']['status'] == 'waiting'
    assert all(t['status'] == 'running' for t in runtime.store.read_state()['tasks'].values())
    for task in runtime.store.read_state()['tasks'].values():
        (path / task['artifact']).write_text('body\nQA DONE\n')
    summary, code = runner.run(max_iterations=5)
    assert code == 0 and summary['runner']['status'] == 'complete'
    assert summary['flows'] == {'A': 'complete', 'B': 'complete'}


def test_runner_reports_dispatch_wait_reason(setup, monkeypatch):
    Runner, _, RunnerLimits = api()
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    start_script(runtime, FakeHerdr())

    def unknown(*a, **k):
        return ResourceReport(8, .1, k['active_workers'], QuotaAvailability('unknown', None), False)

    runner = Runner(path, herdr=FakeHerdr(), resource_check=unknown,
                    sleep=lambda s: None, limits=RunnerLimits(0.5, 1))
    summary, code = runner.run(max_iterations=2)
    assert code == 124
    assert 'quota unknown' in summary['runner']['reason']


def test_runner_partial_or_empty_flows_never_complete(setup, monkeypatch):
    Runner, _, RunnerLimits = api()
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    start_script(runtime, FakeHerdr())
    runner = Runner(path, herdr=FakeHerdr(), resource_check=ready,
                    sleep=lambda s: None, limits=RunnerLimits(0.5, 1))
    state = runtime.store.read_state()
    assert runner._evaluate({'flows': {'A': 'complete'}, 'tasks': {}}, state)[0] == 'continue'
    assert runner._evaluate({'flows': {}, 'tasks': {}}, state)[0] == 'continue'
    assert runner._evaluate({'flows': {'A': 'complete', 'B': 'complete'}, 'tasks': {}}, state)[0] == 'complete'


def test_runner_waits_for_pending_decision_without_inventing_approval(setup, monkeypatch):
    Runner, _, RunnerLimits = api()
    _write_yaml(setup[0] / 'flows/readonly.yaml',
                {'steps': [{'team': 'validator', 'decision': 'user'}], 'max_fail_rounds': 1})
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup, items=('A',))
    adapter = FakeHerdr()
    start_script(runtime, adapter)
    runner = Runner(path, herdr=adapter, resource_check=ready,
                    sleep=lambda s: None, limits=RunnerLimits(0.5, 1))
    summary, code = runner.run(max_iterations=3)
    assert code == 124
    state = runtime.store.read_state()
    assert state['decisions']['A:readonly:s0:r0:decision']['status'] == 'pending'
    assert adapter.prompts == []
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime.decide('A:readonly:s0:r0:decision', 'approved')
    summary, code = runner.run(max_iterations=2)
    assert code == 124 and len(adapter.prompts) == 1
    for task in runtime.store.read_state()['tasks'].values():
        (path / task['artifact']).write_text('body\nQA DONE\n')
    summary, code = runner.run(max_iterations=5)
    assert code == 0 and summary['flows'] == {'A': 'complete'}


def test_runner_stop_control_exits_clean(setup, monkeypatch):
    Runner, _, RunnerLimits = api()
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    adapter = FakeHerdr()
    start_script(runtime, adapter)
    runner = Runner(path, herdr=adapter, resource_check=ready,
                    sleep=lambda s: None, limits=RunnerLimits(0.5, 1))
    runner.run(max_iterations=1)
    runtime.request_control('stop')
    summary, code = runner.run(max_iterations=3)
    assert code == 0 and summary['runner']['status'] == 'stopped'


def test_runner_failed_worker_visible_no_redispatch(setup, monkeypatch):
    Runner, _, RunnerLimits = api()
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    adapter = FakeHerdr()
    start_script(runtime, adapter)
    runner = Runner(path, herdr=adapter, resource_check=ready,
                    sleep=lambda s: None, limits=RunnerLimits(0.5, 1))
    runner.run(max_iterations=1)
    prompts = len(adapter.prompts)
    task_id = next(iter(runtime.store.read_state()['tasks']))
    runtime.store.update_state(lambda s: (s['tasks'][task_id].update(status='failed') or s))
    summary, code = runner.run(max_iterations=3)
    assert code == 1 and summary['runner']['status'] == 'failed'
    assert len(adapter.prompts) == prompts
    assert runtime.store.read_state()['tasks'][task_id]['status'] == 'failed'


def test_runner_exception_is_visible_with_nonzero_exit(setup, monkeypatch):
    Runner, _, RunnerLimits = api()
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    start_script(runtime, FakeHerdr())
    runner = Runner(path, herdr=FakeHerdr(), resource_check=ready,
                    sleep=lambda s: None, limits=RunnerLimits(0.5, 1))

    def boom(**_kwargs):
        raise RuntimeError('herdr unavailable')

    monkeypatch.setattr(runner.runtime, 'tick', boom)
    summary, code = runner.run(max_iterations=5)
    assert code == 1
    assert 'herdr unavailable' in summary['runner']['reason']


def test_runner_signal_exit_is_recorded_and_nonzero(setup, monkeypatch):
    Runner, _, RunnerLimits = api()
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    start_script(runtime, FakeHerdr())

    def kill(_seconds):
        os.kill(os.getpid(), signal.SIGTERM)

    runner = Runner(path, herdr=FakeHerdr(), resource_check=ready,
                    sleep=kill, limits=RunnerLimits(0.5, 1))
    summary, code = runner.run(max_iterations=5)
    assert code == 128 + signal.SIGTERM
    assert summary['runner']['status'] == 'stopped'
    assert 'signal' in summary['runner']['reason']


def test_runner_empty_flows_never_report_false_completion(setup, monkeypatch):
    Runner, _, RunnerLimits = api()
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    start_script(runtime, FakeHerdr())
    runner = Runner(path, herdr=FakeHerdr(), resource_check=ready,
                    sleep=lambda s: None, limits=RunnerLimits(0.1, 0.2))
    verdict, _ = runner._evaluate({'flows': {}, 'tasks': {}}, {})
    assert verdict == 'continue'


HOLDER = (
    "import sys, time\n"
    "from crew.runner import Runner\n"
    "with Runner(sys.argv[1])._ownership():\n"
    "    open(sys.argv[2], 'w').write('ready')\n"
    "    time.sleep(30)\n"
)
ATTEMPT = (
    "import sys\n"
    "from crew.runner import Runner, RunnerError\n"
    "try:\n"
    "    Runner(sys.argv[1]).run(max_iterations=1)\n"
    "except RunnerError as error:\n"
    "    print('RunnerError:' + str(error))\n"
    "    raise SystemExit(3)\n"
    "raise SystemExit(0)\n"
)


def test_two_real_runner_processes_share_one_ownership(setup, monkeypatch):
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    start_script(runtime, FakeHerdr())
    root = str(pathlib.Path(__file__).resolve().parent.parent)
    env = {key: value for key, value in os.environ.items()
           if key not in ('HERDR_PANE_ID', 'HERDR_TAB_ID', 'HERDR_WORKSPACE_ID')}
    env.update({'PYTHONPATH': root, 'CREW_ROLE': 'orchestrator', 'PYTHONDONTWRITEBYTECODE': '1'})
    ready = setup[1] / 'holder.ready'
    holder = subprocess.Popen([sys.executable, '-c', HOLDER, str(path), str(ready)],
                              env=env, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 10
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready.exists(), holder.stderr.read()
        attempt = subprocess.run([sys.executable, '-c', ATTEMPT, str(path)],
                                 env=env, cwd=root, capture_output=True, text=True)
        assert attempt.returncode == 3, attempt.stdout + attempt.stderr
        assert 'already owns' in attempt.stdout
        assert runtime.store.read_state()['runner']['status'] == 'started'
    finally:
        holder.terminate()
        holder.wait(timeout=10)


HOLD_RUN = (
    "import sys, time\n"
    "from crew.runner import Runner\n"
    "runner = Runner(sys.argv[1])\n"
    "with runner._ownership():\n"
    "    runner._mark_start()\n"
    "    open(sys.argv[2], 'w').write('ready')\n"
    "    time.sleep(60)\n"
)


def _cli(setup, root, env, *args):
    return subprocess.run(
        [sys.executable, '-m', 'crew.cli', '--global-dir', str(setup[0]), '--project', str(setup[1]), *args],
        env=env, cwd=root, capture_output=True, text=True)


def test_status_reports_stale_runner_after_sigkill_without_mutating_state(setup, monkeypatch):
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    start_script(runtime, FakeHerdr())
    root = str(pathlib.Path(__file__).resolve().parent.parent)
    env = {key: value for key, value in os.environ.items()
           if key not in ('HERDR_PANE_ID', 'HERDR_TAB_ID', 'HERDR_WORKSPACE_ID')}
    env.update({'PYTHONPATH': root, 'CREW_ROLE': 'orchestrator', 'PYTHONDONTWRITEBYTECODE': '1'})
    ready = setup[1] / 'runner.ready'
    holder = subprocess.Popen([sys.executable, '-c', HOLD_RUN, str(path), str(ready)],
                              env=env, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.time() + 10
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert ready.exists(), holder.stderr.read()
        from crew.runner import observe_ownership
        assert observe_ownership(path) == 'active'
        active = _cli(setup, root, env, 'status', 'run1')
        assert active.returncode == 0, active.stdout + active.stderr
        assert 'runner_ownership: active' in active.stdout, active.stdout
        assert 'runner_stale' not in active.stdout
        # A read must not disturb the active owner.
        assert observe_ownership(path) == 'active'
        before = (path / 'state.json').read_bytes()
        holder.kill()
        holder.wait(timeout=10)
        assert observe_ownership(path) == 'free'
        stale = _cli(setup, root, env, 'status', 'run1')
        report = _cli(setup, root, env, 'report', 'run1')
        assert report.returncode == 0 and 'runner_stale' in report.stdout
        assert stale.returncode == 0, stale.stdout + stale.stderr
        assert 'runner_ownership: free' in stale.stdout
        assert 'runner_stale' in stale.stdout and 'no process holds the run lock' in stale.stdout
        # Status and report reads never rewrite authoritative state.
        assert (path / 'state.json').read_bytes() == before
        shown = _cli(setup, root, env, 'state', 'show', 'run1')
        assert shown.returncode == 0 and 'running' in shown.stdout
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)


def test_runner_detects_previous_crash_without_relying_on_pid(setup, monkeypatch):
    Runner, _, RunnerLimits = api()
    monkeypatch.setenv('CREW_ROLE', 'lead')
    runtime, path = make_run(setup)
    start_script(runtime, FakeHerdr())
    runtime.store.update_state(lambda s: (s['runner'].update(status='running', pid=999999) or s))
    adapter = FakeHerdr()
    runner = Runner(path, herdr=adapter, resource_check=ready,
                    sleep=lambda s: None, limits=RunnerLimits(0.5, 1))
    summary, code = runner.run(max_iterations=1)
    assert summary['runner']['recovered_crashed_runner'] is True
