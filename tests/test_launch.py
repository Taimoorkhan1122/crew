import json
import shlex
import subprocess

import pytest

from crew.config import AxisTool, Role
from crew.herdr import Herdr, HerdrError
from crew.launch import launch


def role(mode, *, readonly=False, args=None, axis=None):
    return Role(
        cli="opencode", herdr_kind="opencode", model="vendor/model", effort="high",
        args=args or ["--model", "{model}", "--effort", "{effort}", "{prompt}"],
        mode=mode, readonly=readonly, prompt=None, skills=[], axis=axis or [],
    )


def response(result):
    return subprocess.CompletedProcess([], 0, json.dumps({"id": "cli:test", "result": result}), "")


def test_interactive_launch_preserves_ids_and_configured_arguments():
    calls = []
    replies = iter([
        response({"tab": {"tab_id": "w2:t3"}, "root_pane": {"pane_id": "w2:p4"}}),
        response({"agent": {"pane_id": "w2:p4", "agent_status": "idle", "agent_session": {"source": "integration:opencode", "agent": "opencode", "kind": "id", "value": "session-7"}}}),
        response({"agent": {"pane_id": "w2:p4", "agent_status": "done", "agent_session": {"source": "integration:opencode", "agent": "opencode", "kind": "id", "value": "session-7"}}}),
    ])

    def run(argv, **kwargs):
        calls.append(argv)
        return next(replies)

    herdr = Herdr("lab", run=run)
    result = launch(herdr, workspace="w2", name="qa", cwd="/tmp/work", role=role("interactive", args=["--model", "{model}", "--effort", "{effort}"]), prompt="do work")
    assert (result.tab_id, result.pane_id, result.agent_session_id, result.status) == ("w2:t3", "w2:p4", "session-7", "done")
    assert result.agent_session == {"source": "integration:opencode", "agent": "opencode", "kind": "id", "value": "session-7"}
    assert calls[1] == ["herdr", "--session", "lab", "agent", "start", "qa", "--kind", "opencode", "--pane", "w2:p4", "--timeout", "60000", "--", "--model", "vendor/model", "--effort", "high"]
    assert calls[2][3:7] == ["agent", "prompt", "w2:p4", "do work"]


def test_oneshot_quotes_untrusted_prompt_and_enforces_readonly_env():
    calls = []
    replies = iter([
        response({"tab": {"tab_id": "w2:t3"}, "root_pane": {"pane_id": "w2:p4"}}),
        response({"pane": {"pane_id": "w2:p4", "tab_id": "w2:t3", "terminal_id": "term"}}),
        response({"process_info": {"pane_id": "w2:p4", "shell_pid": 123}}),
        response({"type": "ok"}),
    ])

    def run(argv, **kwargs):
        calls.append(argv)
        return next(replies)

    herdr = Herdr("lab", run=run)
    message = "fix 'quote'; $(touch /tmp/should-not-exist)"
    result = launch(
        herdr, workspace="w2", name="qa", cwd="/tmp/a b", role=role("oneshot", readonly=True, axis=["pg"]),
        prompt=message, axis={"pg": AxisTool(bin="pg-axi", env=[], write_gate="PG_READ_ONLY", skill=None)},
    )
    assert result.status == "started"
    assert result.process_identity == {"terminal_id": "term", "shell_pid": 123}
    assert calls[1][3:] == ["pane", "get", "w2:p4"]
    assert calls[2][3:] == ["pane", "process-info", "--pane", "w2:p4"]
    command = calls[3][-1]
    assert shlex.split(command) == ["cd", "--", "/tmp/a b", "&&", "env", "PG_READ_ONLY=1", "opencode", "--model", "vendor/model", "--effort", "high", message, "<", "/dev/null"]


def test_readonly_rejects_configured_write_bypass_before_pane_creation():
    herdr = Herdr("lab", run=lambda *args, **kwargs: pytest.fail("must not call Herdr"))
    with pytest.raises(ValueError, match="write gate"):
        launch(herdr, workspace="w2", name="qa", cwd="/tmp", role=role("oneshot", readonly=True, args=["--confirm", "{prompt}"], axis=["pg"]), prompt="work", axis={"pg": AxisTool(bin="pg-axi", env=[], write_gate="--confirm")})


def test_oneshot_requires_explicit_prompt_argument_before_pane_creation():
    herdr = Herdr("lab", run=lambda *args, **kwargs: pytest.fail("must not call Herdr"))
    with pytest.raises(ValueError, match="must contain"):
        launch(herdr, workspace="w2", name="qa", cwd="/tmp", role=role("oneshot", args=["--model", "{model}"]), prompt="work")


@pytest.mark.parametrize(("start_session", "prompt_session", "expected_session", "expected_id"), [
    ({"source": "integration:opencode", "agent": "opencode", "kind": "id", "value": "start-1"}, None, {"source": "integration:opencode", "agent": "opencode", "kind": "id", "value": "start-1"}, "start-1"),
    (None, None, None, None),
    (None, {"source": "integration:opencode", "agent": "opencode", "kind": "path", "value": "/tmp/session.json"}, {"source": "integration:opencode", "agent": "opencode", "kind": "path", "value": "/tmp/session.json"}, None),
])
def test_interactive_launch_preserves_native_session_kind_and_start_fallback(start_session, prompt_session, expected_session, expected_id):
    replies = iter([
        response({"tab": {"tab_id": "w2:t3"}, "root_pane": {"pane_id": "w2:p4"}}),
        response({"agent": {"pane_id": "w2:p4", "agent_status": "idle", "agent_session": start_session}}),
        response({"agent": {"pane_id": "w2:p4", "agent_status": "done", "agent_session": prompt_session}}),
    ])
    herdr = Herdr("lab", run=lambda *args, **kwargs: next(replies))
    result = launch(herdr, workspace="w2", name="qa", cwd="/tmp", role=role("interactive", args=[]), prompt="work")
    assert result.agent_session == expected_session
    assert result.agent_session_id == expected_id


def test_incomplete_tab_create_rethrows_original_error_with_partial_result():
    class RecordingHerdr(Herdr):
        create_error = None

        def create_tab(self, *args, **kwargs):
            try:
                return super().create_tab(*args, **kwargs)
            except HerdrError as error:
                self.create_error = error
                raise

    herdr = RecordingHerdr("lab", run=lambda *args, **kwargs: response({"tab": {"tab_id": "w2:t3"}, "root_pane": {}}))
    with pytest.raises(HerdrError) as caught:
        launch(herdr, workspace="w2", name="qa", cwd="/tmp", role=role("interactive"), prompt="work")

    assert caught.value is herdr.create_error
    assert caught.value.code == "invalid_json"
    assert str(caught.value) == "invalid_json: Herdr tab or pane ID is missing"
    assert caught.value.launch_result.tab_id == "w2:t3"
    assert caught.value.launch_result.pane_id is None
    assert caught.value.launch_result.agent_session is None


def test_oneshot_failed_submission_keeps_captured_shell_identity():
    def run(argv, **kwargs):
        if argv[3:5] == ['tab', 'create']:
            return response({'tab': {'tab_id':'w2:t3'}, 'root_pane': {'pane_id':'w2:p4'}})
        if argv[3:5] == ['pane', 'get']:
            return response({'pane': {'pane_id':'w2:p4', 'tab_id':'w2:t3', 'terminal_id':'term'}})
        if argv[3:5] == ['pane', 'process-info']:
            return response({'process_info': {'pane_id':'w2:p4', 'shell_pid':123}})
        raise RuntimeError('submission uncertain')
    with pytest.raises(RuntimeError, match='submission uncertain') as caught:
        launch(Herdr('lab', run=run), workspace='w2', name='qa', cwd='/private/tmp',
               role=role('oneshot'), prompt='work')
    assert caught.value.launch_result.process_identity == {'terminal_id':'term', 'shell_pid':123}
