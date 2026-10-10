from crew.control import pause, resume, stop, close
from crew.state import StateStore
import pytest

SESSION = {"source": "integration:opencode", "agent": "opencode", "kind": "id", "value": "s1"}


def store_at(tmp_path, status="queued"):
    store = StateStore(tmp_path / "run")
    task = {"status": status}
    if status != "queued":
        task.update(launch_intent="i1", tab_id="w:t1", pane_id="w:p1", mode="interactive", agent_session=SESSION)
    store.write_state({"tasks": {"A": task}, "owned_panes": ["w:p1"] if status != "queued" else []})
    return store


def test_pause_resume_queued(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = store_at(tmp_path)
    assert pause(store, "A")["status"] == "paused"
    assert resume(store, "A")["status"] == "queued"
    assert stop(store, "A")["status"] == "failed"


def test_active_control_keeps_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = store_at(tmp_path, "running")
    assert pause(store, "A")["status"] == "running"
    assert stop(store, "A")["status"] == "running"
    task = store.read_state()["tasks"]["A"]
    assert task["pause_requested"] and task["stop_requested"]
    assert task["launch_intent"] == "i1"


def test_close_owned_done_matching_idle(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = store_at(tmp_path, "done")
    class Fake:
        def __init__(self): self.calls = []
        def get_agent(self, pane):
            self.calls.append(("get", pane))
            return {"pane_id": pane, "agent_session": SESSION, "agent_status": "idle"}
        def command(self, *args): self.calls.append(args)
    fake = Fake()
    assert close(store, "A", herdr=fake)["closed"] is True
    assert fake.calls == [("get", "w:p1"), ("pane", "close", "w:p1")]
    assert store.read_state()["owned_panes"] == []
    assert close(store, "A", herdr=fake)["closed"] is True
    assert len(fake.calls) == 2


def test_close_unowned_no_external_call(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = store_at(tmp_path, "done")
    store.update_state(lambda s: (s.update(owned_panes=[]) or s))
    class Fake:
        def get_agent(self, pane): raise AssertionError("external call")
    assert close(store, "A", herdr=Fake()).get("closed") is not True


def test_close_mismatch_keeps_resource_and_allows_recheck(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = store_at(tmp_path, "done")
    class Fake:
        def get_agent(self, pane):
            return {"pane_id": pane, "agent_session": {**SESSION, "value": "other"}, "agent_status": "idle"}
        def command(self, *args): raise AssertionError("close called")
    task = close(store, "A", herdr=Fake())
    assert task["reconciliation_needed"] is True
    assert task.get("close_attempted") is None
    assert store.read_state()["owned_panes"] == ["w:p1"]


def test_close_failed_call_never_retries_blindly(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = store_at(tmp_path, "done")
    class Fake:
        calls = 0
        def get_agent(self, pane):
            return {"pane_id": pane, "agent_session": SESSION, "agent_status": "done"}
        def command(self, *args):
            self.calls += 1
            raise RuntimeError("unknown outcome")
    fake = Fake()
    assert close(store, "A", herdr=fake)["close_attempted"] is True
    assert close(store, "A", herdr=fake)["close_attempted"] is True
    assert fake.calls == 1
    assert store.read_state()["owned_panes"] == ["w:p1"]


@pytest.mark.parametrize("change", [
    "status_and_ownership", "status", "ownership", "second_owner", "duplicate_ownership",
    "close_intent", "removed_intent", "close_attempted", "closed",
    "launch_intent", "tab_id", "pane_id", "agent_session", "mode",
])
def test_close_revalidates_state_after_agent_lookup(tmp_path, monkeypatch, change):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = store_at(tmp_path, "done")
    changed = {}

    class Fake:
        def get_agent(self, pane):
            assert pane == "w:p1"
            def mutate(state):
                task = state["tasks"]["A"]
                if change in {"status", "status_and_ownership"}:
                    task["status"] = "failed"
                if change in {"ownership", "status_and_ownership"}:
                    state["owned_panes"] = []
                elif change == "second_owner":
                    state["tasks"]["B"] = {"status": "running", "pane_id": pane}
                elif change == "duplicate_ownership":
                    state["owned_panes"].append(pane)
                elif change == "removed_intent":
                    task.pop("close_intent")
                elif change in {"close_attempted", "closed"}:
                    task[change] = True
                elif change == "agent_session":
                    task[change]["value"] = "s2"
                elif change == "mode":
                    task[change] = "oneshot"
                elif change in {"close_intent", "launch_intent", "tab_id", "pane_id"}:
                    task[change] = "replacement"
                task["pause_requested"] = True
                task["stop_requested"] = True
                return state
            changed.update(store.update_state(mutate))
            return {"pane_id": pane, "agent_session": SESSION, "agent_status": "idle"}

        def command(self, *args):
            pytest.fail("close called after durable state changed")

    task = close(store, "A", herdr=Fake())
    assert task["reconciliation_needed"] is True
    assert task["close_error"]
    assert task.get("close_attempted") == changed["tasks"]["A"].get("close_attempted")
    assert task.get("closed") == changed["tasks"]["A"].get("closed")
    assert task["pause_requested"] and task["stop_requested"]
    for key in ("status", "mode", "launch_intent", "tab_id", "pane_id", "agent_session"):
        assert task[key] == changed["tasks"]["A"][key]
    if change == "close_intent":
        assert task["close_intent"] == "replacement"
    assert store.read_state()["owned_panes"] == changed["owned_panes"]
