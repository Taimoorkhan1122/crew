"""Human presentation uses real CLI operations and retains structured read views."""
import io
import pytest
from crew.cli import main as cli_main
from unittest.mock import patch
from test_config import _global_config

OUTPUT = None

def main(args):
    with patch("sys.stdout", OUTPUT):
        return cli_main(args)


class Terminal(io.StringIO):
    def isatty(self):
        return True

@pytest.fixture
def cli(tmp_path, monkeypatch):
    root = _global_config(tmp_path / 'global')
    project = tmp_path / 'project'
    project.mkdir()
    output = Terminal()
    monkeypatch.setattr(__name__ + ".OUTPUT", output)
    yield ['--global-dir', str(root), '--project', str(project)], output, project

def test_terminal_init_reports_paths_and_repeat_copy_count(cli):
    args, output, project = cli
    assert main(args + ['project', 'init']) == 0
    text = output.getvalue()
    assert 'Crew project initialized' in text
    assert f'Project path: {project}' in text
    assert f'Configuration: {project}/.crew/project.yaml' in text
    assert 'files copied' in text
    output.seek(0); output.truncate()
    assert main(args + ['project', 'init']) == 0
    assert '0 files copied' in output.getvalue()
    assert 'Existing files were preserved' in output.getvalue()

@pytest.mark.parametrize('flags', [['--toon'], ['--full'], ['--fields', 'status']])
def test_terminal_detailed_or_toon_output_remains_structured(cli, flags):
    args, output, _ = cli
    assert main(args + ['config', 'validate'] + flags) == 0
    assert output.getvalue() == 'status: valid\n'

def test_pipe_output_keeps_toon(cli, monkeypatch):
    args, _, _ = cli
    output = io.StringIO()
    monkeypatch.setattr(__name__ + ".OUTPUT", output)
    assert main(args + ['config', 'validate']) == 0
    assert output.getvalue() == 'status: valid\n'

def test_terminal_error_keeps_exit_and_action(cli):
    args, output, _ = cli
    assert main(args + ['bad-command']) == 2
    assert 'Error:' in output.getvalue()
    assert 'crew --help' in output.getvalue()

@pytest.mark.parametrize('flags', [['--human', 'config', 'validate'], ['config', 'validate', '--human']])
def test_explicit_human_on_pipe(cli, monkeypatch, flags):
    args, _, _ = cli
    output = io.StringIO()
    monkeypatch.setattr(__name__ + ".OUTPUT", output)
    assert main(args + flags) == 0
    assert 'Configuration is valid' in output.getvalue()

def test_toon_human_conflict_across_parser_levels(cli):
    args, output, project = cli
    assert main(args + ['--toon', 'project', 'init', '--human']) == 2
    assert not (project / '.crew').exists()

def test_human_empty_status_and_cooperative_stop(cli):
    args, output, _ = cli
    assert main(args + ['run', 'new', '--id', 'demo', '--flow', 'readonly', '--items', 'A']) == 0
    output.seek(0); output.truncate()
    assert main(args + ['status', 'demo']) == 0
    assert 'Tasks: none' in output.getvalue()
    output.seek(0); output.truncate()
    assert main(args + ['stop', 'demo']) == 0
    text = output.getvalue()
    assert 'Stop requested' in text
    assert 'orchestrator' in text
    assert 'Control requests:' in text
    assert 'Run stopped' not in text

def test_cleanup_retains_reasons_and_unknown_readiness():
    from crew.human import render
    text = render({'cleanup': {'panes': [{'pane_id': 'p1', 'status': 'retained', 'reason': 'ownership uncertain'}]},
                   'integration': 'unknown', 'tools': []}, command='doctor')
    assert 'ownership uncertain' in text and 'retained' in text
    assert 'Integration: unknown' in text and 'Tools: none' in text


def test_init_next_command_validates_selected_context_from_another_directory(tmp_path, monkeypatch):
    """Dropping context or quoting makes the displayed command fail or inspect another project."""
    import os
    from pathlib import Path
    import shlex
    import subprocess
    import sys

    root = _global_config(tmp_path / 'global config')
    project = tmp_path / 'selected project'
    project.mkdir()
    output = Terminal()
    monkeypatch.setattr(__name__ + '.OUTPUT', output)
    assert main(['--global-dir', str(root), '--project', str(project), 'project', 'init']) == 0
    command = next(line.removeprefix('Next: ') for line in output.getvalue().splitlines() if line.startswith('Next: '))
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    shim = bin_dir / 'crew'
    shim.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -m crew.cli "$@"\n')
    shim.chmod(0o755)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    empty_home = tmp_path / 'empty home'
    empty_home.mkdir()
    result = subprocess.run(command, shell=True, cwd=elsewhere, capture_output=True, text=True,
                            env={**os.environ, 'HOME': str(empty_home), 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
                                 'PYTHONPATH': str(Path(__file__).resolve().parents[1]), 'PYTHONDONTWRITEBYTECODE': '1'})
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == 'status: valid\n'


def test_generated_run_next_command_reuses_selected_context(cli):
    import shlex
    args, output, project = cli
    assert main(args + ['run', 'new', '--id', 'demo', '--flow', 'readonly', '--items', 'A']) == 0
    command = next(line.strip().removeprefix('- ') for line in output.getvalue().splitlines() if 'crew ' in line)
    assert shlex.split(command) == ['crew', *args, 'run', 'start', 'demo']


def test_displayed_uppercase_decision_id_resolves_exact_saved_decision(cli, monkeypatch):
    """Formatting a dynamic key must not produce an unusable decision identifier."""
    from pathlib import Path
    from crew.runtime import Runtime, create_run
    from test_config import _write_yaml
    from test_runtime_cli import FakeHerdr, ready

    args, output, project = cli
    root = Path(args[1])
    _write_yaml(root / 'flows/readonly.yaml', {'steps': [{'team': 'validator', 'decision': 'user'}], 'max_fail_rounds': 1})
    monkeypatch.setenv('CREW_ROLE', 'orchestrator')
    runtime = Runtime(create_run(root, project, 'case', 'readonly', ['BUG-42']))
    adapter = FakeHerdr()
    runtime.start(herdr=adapter)
    runtime.tick(herdr=adapter, resource_check=ready)
    saved_id = next(iter(runtime.store.read_state()['decisions']))
    assert saved_id == 'BUG-42:readonly:s0:r0:decision'
    monkeypatch.setenv('CREW_ROLE', 'lead')
    assert main(args + ['status', 'case']) == 0
    displayed_id = next(line.strip().removesuffix(':') for line in output.getvalue().splitlines() if ':readonly:s0:r0:decision' in line)
    assert main(args + ['decide', 'case', displayed_id, 'approved']) == 0
    assert runtime.store.read_state()['decisions'][saved_id]['status'] == 'approved'


def test_dynamic_map_keys_keep_case_and_underscores_even_when_named_like_schema():
    from crew.human import render
    text = render({'decisions': {'status': {'status': 'pending'}, 'MiXeD_ID': {'status': 'pending'}},
                   'flows': {'BUG_42': 'blocked'}, 'My_Resource': {'reason': 'ownership uncertain'}})
    assert '  status:' in text
    assert '  MiXeD_ID:' in text
    assert '  BUG_42: blocked' in text
    assert 'My_Resource:' in text


@pytest.fixture
def active_run(cli, monkeypatch):
    from pathlib import Path
    from crew.runtime import Runtime, create_run
    from test_runtime_cli import FakeHerdr, ready
    args, output, project = cli
    monkeypatch.setenv('CREW_ROLE', 'orchestrator')
    runtime = Runtime(create_run(Path(args[1]), project, 'summary', 'readonly', ['BUG-42', 'B']))
    adapter = FakeHerdr()
    started = runtime.start(herdr=adapter)
    runtime.tick(herdr=adapter, resource_check=ready)
    task_id = next(iter(runtime.store.read_state()['tasks']))
    def uncertain(state):
        state['tasks'][task_id].update(status='blocked', reconciliation_needed=True,
                                       launch_error='native identity uncertain', close_error='active writer retained')
        state['cleanup'] = {'panes': {'w1:p1': {'status': 'retained', 'reason': 'active writer'}}}
        return state
    runtime.store.update_state(uncertain)
    return runtime, adapter, started, task_id


def test_control_summary_exposes_risks_without_runtime_internals(cli, active_run, monkeypatch):
    args, output, _ = cli
    runtime, _, _, task_id = active_run
    monkeypatch.setenv('CREW_ROLE', 'lead')
    assert main(args + ['stop', 'summary']) == 0
    text = output.getvalue()
    assert 'Stop requested' in text and 'orchestrator' in text
    assert 'Control requests:' in text and 'stop' in text
    assert task_id in text and 'blocked: 1' in text and 'running: 1' in text
    assert 'native identity uncertain' in text and 'active writer' in text
    assert 'w1:p1' in text and 'w1:p2' in text
    state = runtime.store.read_state()
    assert state['config_digest'] not in text
    assert state['tasks'][task_id]['launch_intent'] not in text
    assert 'Agent session' not in text and 'created_ns' not in text
    assert len(text.splitlines()) <= 30
    output.seek(0); output.truncate()
    assert main(args + ['stop', 'summary', '--toon']) == 0
    assert state['config_digest'] in output.getvalue()
    assert state['tasks'][task_id]['launch_intent'] in output.getvalue()


def test_status_and_report_keep_authoritative_task_warnings(cli, active_run):
    args, output, _ = cli
    _, _, _, task_id = active_run
    for command in ('status', 'report'):
        output.seek(0); output.truncate()
        assert main(args + [command, 'summary']) == 0
        text = output.getvalue()
        assert task_id in text and 'native identity uncertain' in text
        assert 'Reconciliation needed: yes' in text
        assert 'blocked: 1' in text and 'running: 1' in text


def test_start_and_dispatch_summarize_real_results_without_claiming_new_completion(cli, active_run):
    from crew.human import render
    from test_runtime_cli import ready
    _, _, project = cli
    runtime, adapter, started, task_id = active_run
    first = render(started, command='run', operation='start')
    repeated = render(runtime.start(herdr=adapter), command='run', operation='start')
    assert 'Workspace: w1' in first and 'Tasks: none' in first
    assert len(first.splitlines()) <= 12
    assert 'native identity uncertain' in repeated and 'Run created' not in repeated
    actual = runtime.dispatch(task_id, herdr=adapter, resource_check=ready)
    text = render(actual, command='dispatch', context={'run': 'summary', 'task': task_id, 'project': str(project)})
    assert task_id in text and 'Status: blocked' in text
    assert 'Reconciliation needed: yes' in text and 'native identity uncertain' in text
    assert 'active writer retained' in text
    assert actual['launch_intent'] not in text
    assert len(text.splitlines()) <= 12


def test_cleanup_summary_keeps_every_retained_reason_and_only_counts_safe_results():
    from crew.human import render
    completed = {f'DONE_{i}': {'status': 'done', 'closed': True} for i in range(100)}
    blocked = {f'BUG_{i}': {'status': 'blocked', 'launch_error': f'failure {i}'} for i in range(15)}
    result = {'run_id': 'large', 'tasks': {**completed, **blocked},
              'cleanup': {'active_tasks': ['BUG_0'], 'stopped': False, 'closed': False,
                          'orchestrator': {'pane_id': 'Orch_1', 'closed': False, 'reason': 'live identity unknown'},
                          'retained_branches': [{'repository': '/repo', 'branch': 'Crew_FIX'}],
                          'retained_resources': [{'kind': 'workspace', 'id': 'Work_SPACE', 'reason': 'shell retained'}],
                          'worktrees': [{'path': '/repo/worktree', 'status': 'retained', 'reason': 'dirty worktree'}]}}
    text = render(result, command='close', context={'run': 'large', 'project': '/project'})
    assert 'Close requested' in text and 'done: 100' in text
    assert 'DONE_99' not in text
    assert all(f'failure {i}' in text and f'BUG_{i}' in text for i in range(15))
    assert all(value in text for value in ('BUG_0', 'Orch_1', 'live identity unknown', 'Crew_FIX', 'Work_SPACE',
                                           'shell retained', 'dirty worktree', '/repo/worktree'))
    assert 'Stopped: no' in text and 'Closed: no' in text
    assert '--full' in text or '--toon' in text
    assert len(text.splitlines()) < 75


def test_dispatch_detail_hint_reads_state_without_requesting_another_dispatch(cli, active_run):
    import shlex
    args, output, _ = cli
    runtime, _, _, task_id = active_run
    before = runtime.store.read_state()
    assert main(args + ['dispatch', 'summary', task_id]) == 0
    command = next(line.removeprefix('Details: ') for line in output.getvalue().splitlines() if line.startswith('Details: '))
    assert shlex.split(command) == ['crew', *args, 'state', 'show', 'summary', '--full']
    assert runtime.store.read_state() == before


def test_runtime_details_command_reads_same_spaced_project_from_other_directory(tmp_path, monkeypatch):
    """The public CLI hint must retain selected paths when reused from another directory."""
    import os
    from pathlib import Path
    import shlex
    import subprocess
    import sys
    from crew.runtime import Runtime, create_run

    root = _global_config(tmp_path / 'selected config')
    project = tmp_path / 'selected project'
    project.mkdir()
    runtime = Runtime(create_run(root, project, 'selected', 'readonly', ['A']))
    output = Terminal()
    monkeypatch.setattr(__name__ + '.OUTPUT', output)
    args = ['--global-dir', str(root), '--project', str(project)]
    assert main(args + ['status', 'selected']) == 0
    displayed = next(line.removeprefix('Details: ') for line in output.getvalue().splitlines() if line.startswith('Details: '))
    assert shlex.split(displayed) == ['crew', *args, 'state', 'show', 'selected', '--full']
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    shim = bin_dir / 'crew'
    shim.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -m crew.cli "$@"\n')
    shim.chmod(0o755)
    elsewhere = tmp_path / 'other directory'
    elsewhere.mkdir()
    empty_home = tmp_path / 'empty home'
    empty_home.mkdir()
    result = subprocess.run(displayed, shell=True, cwd=elsewhere, capture_output=True, text=True,
                            env={**os.environ, 'HOME': str(empty_home), 'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
                                 'PYTHONPATH': str(Path(__file__).resolve().parents[1]), 'PYTHONDONTWRITEBYTECODE': '1'})
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'run_id: selected\n' in result.stdout

