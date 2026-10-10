import threading
import time
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from crew.config import Handoff, Team
from crew.state import StateStore
from crew.tick import tick


SESSION = {"source": "integration:opencode", "agent": "opencode", "kind": "path", "value": "/tmp/session"}
TEAM = Team(roles=["worker"], handoffs={"worker": Handoff(**{"in": "input.md", "out": "output.md"})}, done_marker="TASK DONE", axis=[])


def setup(tmp_path, *, status="running", mode="interactive", session=SESSION):
    store = StateStore(tmp_path / "run")
    store.write_state({"tasks": {"A": {"status": status, "mode": mode, "role": "worker", "launch_intent": "intent-1", "launch_started_at_ns": time.time_ns(), "tab_id": "w1:t2", "pane_id": "w1:p3", "agent_session": session}}, "owned_panes": ["w1:p3"]})
    return store


class Agent:
    def __init__(self, status="done", session=SESSION):
        self.status, self.session = status, session
        self.calls = []

    def get_agent(self, pane):
        self.calls.append(pane)
        return {"pane_id": pane, "agent_status": self.status, "agent_session": self.session}


def call(store, agent):
    return tick(store, "A", herdr=agent, team=TEAM)


def evidence(store, content="result\nTASK DONE\n"):
    (store.run_dir / "output.md").write_text(content)


def test_role_gate_precedes_mutation_and_herdr(tmp_path, monkeypatch):
    store = setup(tmp_path)
    monkeypatch.delenv("CREW_ROLE", raising=False)
    with pytest.raises(PermissionError):
        call(store, Agent())
    assert store.read_state()["tasks"]["A"]["status"] == "running"


@pytest.mark.parametrize("content,status", [(None, "done"), ("result", "done"), ("TASK DONE", "working"), ("TASK DONE", "blocked")])
def test_completion_needs_file_marker_and_settled_status(tmp_path, monkeypatch, content, status):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    if content is not None:
        evidence(store, content)
    result = call(store, Agent(status))
    assert result["status"] == ("blocked" if status == "blocked" else "running")


def test_matching_snapshot_and_complete_output_finishes_once(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    evidence(store)
    agent = Agent()
    assert call(store, agent)["status"] == "done"
    assert call(store, agent)["status"] == "done"
    assert agent.calls == ["w1:p3"]
    assert store.read_state()["owned_panes"] == ["w1:p3"]


def test_marker_alone_and_unowned_pane_cannot_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    evidence(store, "TASK DONE\n")
    assert call(store, Agent())["status"] == "running"
    evidence(store)
    store.update_state(lambda state: (state.update(owned_panes=[]) or state))
    result = call(store, Agent())
    assert result["status"] == "blocked"
    assert result["reconciliation_needed"] is True


def test_blocked_task_recovers_only_with_matching_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path, status="blocked")
    evidence(store)
    assert call(store, Agent())["status"] == "done"


def test_stale_output_and_inflight_dispatch_do_not_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    evidence(store)
    old = store.read_state()["tasks"]["A"]["launch_started_at_ns"] - 1_000_000_000
    os.utime(store.run_dir / "output.md", ns=(old, old))
    assert call(store, Agent())["status"] == "running"
    store.update_state(lambda state: (state["tasks"]["A"].update(status="queued") or state))
    agent = Agent()
    assert call(store, agent)["status"] == "queued"
    assert agent.calls == []


@pytest.mark.parametrize("session", [None, {**SESSION, "value": "/tmp/other"}])
def test_missing_or_mismatched_session_blocks_without_replacing_identity(tmp_path, monkeypatch, session):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    evidence(store)
    result = call(StateStore(store.run_dir), Agent(session=session))
    assert result["status"] == "blocked"
    assert result["reconciliation_needed"] is True
    assert result["agent_session"] == SESSION


def test_partial_identity_and_get_error_remain_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path, status="blocked", session=None)
    evidence(store)
    assert call(store, Agent())["status"] == "blocked"
    class Broken:
        def get_agent(self, pane):
            raise RuntimeError("snapshot failed")
    assert call(store, Broken())["reconciliation_needed"] is True


def test_oneshot_marker_and_disappeared_pane_do_not_prove_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path, mode="oneshot", session=None)
    evidence(store)
    result = call(store, Agent(session=None))
    assert result["status"] == "blocked"
    assert result["reconciliation_needed"] is True


def test_concurrent_tick_does_not_restore_terminal_state(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    evidence(store)
    entered, release = threading.Event(), threading.Event()
    class Slow(Agent):
        def get_agent(self, pane):
            entered.set()
            assert release.wait(3)
            return super().get_agent(pane)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(call, store, Slow())
        assert entered.wait(3)
        store.update_state(lambda state: (state["tasks"]["A"].update(status="failed") or state))
        release.set()
        assert pending.result(timeout=3)["status"] == "failed"


def test_concurrent_tick_does_not_overwrite_queued_transition(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    evidence(store)
    entered, release = threading.Event(), threading.Event()
    class Slow(Agent):
        def get_agent(self, pane):
            entered.set()
            assert release.wait(3)
            return super().get_agent(pane)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(call, store, Slow())
        assert entered.wait(3)
        store.update_state(lambda state: (state["tasks"]["A"].update(status="queued") or state))
        release.set()
        assert pending.result(timeout=3)["status"] == "queued"


@pytest.mark.parametrize("timestamp", [True, False, 0, -1, 1.5])
def test_invalid_launch_timestamp_never_completes(tmp_path, monkeypatch, timestamp):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    store.update_state(lambda state: (state["tasks"]["A"].update(launch_started_at_ns=timestamp) or state))
    evidence(store)
    result = call(store, Agent())
    assert result["status"] == "blocked"
    assert result["reconciliation_needed"] is True


@pytest.mark.parametrize("session", [{}, {"source": "", "agent": "opencode", "kind": "id", "value": "s"}, {"source": "integration:opencode", "agent": " ", "kind": "id", "value": "s"}, {"source": "integration:opencode", "agent": "opencode", "kind": "other", "value": "s"}, {"source": "integration:opencode", "agent": "opencode", "kind": "path", "value": ""}])
def test_malformed_matching_native_session_never_completes(tmp_path, monkeypatch, session):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path, session=session)
    evidence(store)
    result = call(store, Agent(session=session))
    assert result["status"] == "blocked"
    assert result["agent_session"] == session


def test_replaced_output_cannot_pair_old_bytes_with_new_timestamp(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    output = store.run_dir / "output.md"
    evidence(store)
    original = type(output).open
    class RacingFile:
        def __init__(self, file):
            self.file = file
        def __enter__(self):
            self.file.__enter__()
            return self
        def __exit__(self, *args):
            return self.file.__exit__(*args)
        def fileno(self):
            return self.file.fileno()
        def read(self):
            content = self.file.read()
            replacement = store.run_dir / "replacement.md"
            replacement.write_text("new output without marker\n")
            os.replace(replacement, output)
            return content
    def racing_open(path, *args, **kwargs):
        file = original(path, *args, **kwargs)
        return RacingFile(file) if path == output else file
    monkeypatch.setattr(type(output), "open", racing_open)
    assert call(store, Agent())["status"] != "done"


@pytest.mark.parametrize("kind", [["id"], {"kind": "id"}])
@pytest.mark.parametrize("identity_source", ["persisted", "live"])
def test_unhashable_session_kind_blocks_and_preserves_reconciliation(
        tmp_path, monkeypatch, kind, identity_source):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    evidence(store)
    malformed = {**SESSION, "kind": kind}
    if identity_source == "persisted":
        store.update_state(lambda state: (
            state["tasks"]["A"].update(agent_session=malformed) or state))
        agent = Agent()
    else:
        agent = Agent(session=malformed)

    result = call(store, agent)

    assert result["status"] == "blocked"
    assert result["reconciliation_needed"] is True
    assert result["agent_session"] == (malformed if identity_source == "persisted" else SESSION)
    assert result["reconciliation_error"] == (
        "native agent session is unavailable" if identity_source == "persisted"
        else "live agent identity does not match launch")
    assert store.read_state()["tasks"]["A"]["reconciliation_needed"] is True
    assert agent.calls == ([] if identity_source == "persisted" else ["w1:p3"])
