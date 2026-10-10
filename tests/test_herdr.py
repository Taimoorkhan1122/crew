import json
import subprocess

import pytest

from crew.herdr import Herdr, HerdrError


def reply(result, *, code=0):
    return subprocess.CompletedProcess([], code, json.dumps({"id": "cli:test", "result": result}), "")


def test_scoped_json_and_owned_cleanup():
    calls = []
    responses = iter([
        reply({"tab": {"tab_id": "w2:t3"}, "root_pane": {"pane_id": "w2:p4"}}),
        reply({"type": "ok"}),
    ])

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return next(responses)

    herdr = Herdr("crew-lab", run=run)
    assert herdr.create_tab("w2", "/tmp/work", "worker") == ("w2:t3", "w2:p4")
    herdr.close_owned("w2:p4")
    assert calls[0][0] == ["herdr", "--session", "crew-lab", "tab", "create", "--workspace", "w2", "--cwd", "/tmp/work", "--label", "worker", "--no-focus"]
    assert calls[1][0] == ["herdr", "--session", "crew-lab", "pane", "close", "w2:p4"]
    with pytest.raises(ValueError, match="not owned"):
        herdr.close_owned("w2:p1")
    assert len(calls) == 2


@pytest.mark.parametrize("response", [
    subprocess.CompletedProcess([], 0, "not-json", ""),
    subprocess.CompletedProcess([], 1, "", '{"error":{"code":"pane_not_found","message":"gone"}}'),
    reply({"other": 1}),
])
def test_malformed_error_or_unexpected_json_fails_closed(response):
    herdr = Herdr("lab", run=lambda *args, **kwargs: response)
    with pytest.raises(HerdrError):
        herdr.create_tab("w2", "/tmp", "worker")


@pytest.mark.parametrize("failure", ["agent_prompt_stalled", "timeout"])
@pytest.mark.parametrize("output", ["still running\n", ""])
def test_prompt_timeout_inspects_once_without_resend(failure, output):
    calls = []
    responses = iter([
        subprocess.CompletedProcess([], 1, "", json.dumps({"error": {"code": failure, "message": "uncertain"}})),
        reply({"agent": {"pane_id": "w2:p4", "agent_status": "working", "agent_session_id": "abc"}, "type": "agent_info"}),
        subprocess.CompletedProcess([], 0, output, ""),
    ])

    def run(argv, **kwargs):
        calls.append(argv)
        return next(responses)

    outcome = Herdr("lab", run=run).prompt("w2:p4", "work", timeout_ms=1000)
    assert outcome.status == "uncertain"
    assert outcome.agent["agent_status"] == "working"
    assert outcome.output == output
    assert [call[3:5] for call in calls] == [["agent", "prompt"], ["agent", "get"], ["agent", "read"]]


def test_agent_read_preserves_json_error_on_nonzero_exit():
    response = subprocess.CompletedProcess([], 1, "", '{"error":{"code":"agent_not_found","message":"missing"}}')
    herdr = Herdr("lab", run=lambda *args, **kwargs: response)
    with pytest.raises(HerdrError) as error:
        herdr.read_agent("w2:p4")
    assert error.value.code == "agent_not_found"
