import threading
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from crew.config import Resources, Role
from crew.dispatch import dispatch
from crew.herdr import Herdr
from crew.launch import LaunchResult
from crew.resources import QuotaAvailability, ResourceReport
from crew.state import StateStore

ROLE = Role(cli="opencode", herdr_kind="opencode", model="vendor/model", effort="low", args=["{prompt}"], mode="interactive", readonly=False, skills=[], axis=[])
LIMITS = Resources(min_free_mem_gb=2, max_load_per_core=1, max_workers=2, min_quota_runway_seconds=60)


def setup(tmp_path):
    store = StateStore(tmp_path / "run")
    store.write_state({"tasks": {"A": {"status": "queued"}}})
    return store


def ready(*args, **kwargs):
    return ResourceReport(4, 0.2, kwargs["active_workers"], QuotaAvailability("available", 180), True)


def call(store, herdr, **kwargs):
    kwargs.setdefault("resource_check", ready)
    role = kwargs.pop("role", ROLE)
    return dispatch(store, "A", herdr=herdr, workspace="w1", name="qa", cwd="/tmp/work", role=role, prompt="work", limits=LIMITS, **kwargs)


def test_orchestrator_role_required_before_mutation(tmp_path, monkeypatch):
    store = setup(tmp_path)
    monkeypatch.delenv("CREW_ROLE", raising=False)
    with pytest.raises(PermissionError):
        call(store, object(), launch_role=lambda *a, **k: pytest.fail("launched"))
    assert store.read_state()["tasks"]["A"] == {"status": "queued"}


@pytest.mark.parametrize("report", [
    ResourceReport(None, None, 0, QuotaAvailability("unknown", None), False),
    ResourceReport(1, 0.2, 0, QuotaAvailability("available", 180), False),
    ResourceReport(4, 0.2, 0, QuotaAvailability("unavailable", 10), False),
])
def test_resource_or_quota_failure_stays_queued(tmp_path, monkeypatch, report):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    result = call(store, object(), resource_check=lambda *a, **k: report, launch_role=lambda *a, **k: pytest.fail("launched"))
    assert result["status"] == "queued"
    assert "launch_intent" not in store.read_state()["tasks"]["A"]


@pytest.mark.parametrize("status,expected", [("done", "running"), ("failed", "running"), ("paused", "running"), ("blocked", "queued"), ("running", "queued")])
def test_only_nonterminal_intents_consume_capacity(tmp_path, monkeypatch, status, expected):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    store.update_state(lambda state: (state["tasks"].update(B={"status": status, "launch_intent": "old"}) or state))
    one_worker = Resources(min_free_mem_gb=2, max_load_per_core=1, max_workers=1, min_quota_runway_seconds=60)
    result = dispatch(store, "A", herdr=object(), workspace="w1", name="qa", cwd="/tmp/work", role=ROLE, prompt="work", limits=one_worker, resource_check=ready, launch_role=lambda *a, **k: LaunchResult("w1:t2", "w1:p3", "started"))
    assert result["status"] == expected


def test_success_persists_intent_then_identity_and_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    session = {"source": "integration:opencode", "agent": "opencode", "kind": "path", "value": "/tmp/session"}
    def launch_role(*args, **kwargs):
        task = store.read_state()["tasks"]["A"]
        assert task["launch_intent"]
        assert task["status"] == "queued"
        return LaunchResult("w1:t2", "w1:p3", "done", session)
    result = call(store, object(), launch_role=launch_role)
    assert result["status"] == "running"
    assert result["tab_id"] == "w1:t2"
    assert result["pane_id"] == "w1:p3"
    assert result["agent_session"] == session
    assert result["mode"] == "interactive"
    assert result["role"] == "qa"
    assert isinstance(result["launch_started_at_ns"], int)
    assert store.read_state()["owned_panes"] == ["w1:p3"]
    assert call(store, object(), launch_role=lambda *a, **k: pytest.fail("relaunched")) == result


def test_concurrent_dispatch_launches_once(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls = []
    def launch_role(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        return LaunchResult("w1:t2", "w1:p3", "started")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(call, store, object(), launch_role=launch_role)
        assert entered.wait(3)
        second = pool.submit(call, store, object(), launch_role=launch_role)
        assert second.result(timeout=3)["reconciliation_needed"] is True
        release.set()
        assert first.result(timeout=3)["status"] == "running"
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [False, True])
def test_uncertainty_or_error_keeps_evidence_and_never_relaunches(tmp_path, monkeypatch, failure):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    class Adapter:
        owned_panes = set()
    adapter = Adapter()
    def launch_role(*args, **kwargs):
        adapter.owned_panes.add("w1:p3")
        if failure:
            raise RuntimeError("launch failed")
        return LaunchResult("w1:t2", "w1:p3", "uncertain", {"kind": "id", "value": "s1"}, "observed")
    result = call(store, adapter, launch_role=launch_role)
    assert result["reconciliation_needed"] is True
    assert result["status"] == "blocked"
    assert result["launch_intent"]
    assert "w1:p3" in store.read_state()["owned_panes"]
    if failure:
        assert result["launch_error"] == "launch failed"
    else:
        assert result["output"] == "observed"
        assert result["agent_session"]["value"] == "s1"
    restarted = StateStore(store.run_dir)
    assert call(restarted, Adapter(), launch_role=lambda *a, **k: pytest.fail("relaunched")) == result


def test_existing_restart_intent_remains_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    store.update_state(lambda state: {**state, "tasks": {"A": {"status": "queued", "launch_intent": "old"}, "B": {"status": "queued"}}})
    result = call(StateStore(store.run_dir), object(), launch_role=lambda *a, **k: pytest.fail("relaunched"))
    assert result["launch_intent"] == "old"
    assert result["reconciliation_needed"] is True
    assert store.read_state()["tasks"]["B"] == {"status": "queued"}


def test_unknown_launch_status_requires_reconciliation(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    result = call(store, object(), launch_role=lambda *a, **k: LaunchResult("w1:t2", "w1:p3", "unknown"))
    assert result["status"] == "blocked"
    assert result["reconciliation_needed"] is True


@pytest.mark.parametrize("kind,value", [("id", "session-7"), ("path", "/tmp/session-7")])
def test_prompt_failure_after_start_keeps_partial_identity(tmp_path, monkeypatch, kind, value):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    session = {"source": "integration:opencode", "agent": "opencode", "kind": kind, "value": value}
    replies = iter([
        subprocess.CompletedProcess([], 0, json.dumps({"result": {"tab": {"tab_id": "w1:t9"}, "root_pane": {"pane_id": "w1:p7"}}}), ""),
        subprocess.CompletedProcess([], 0, json.dumps({"result": {"agent": {"pane_id": "w1:p7", "agent_status": "idle", "agent_session": session}}}), ""),
        subprocess.CompletedProcess([], 1, "", json.dumps({"error": {"code": "command_failed", "message": "prompt response malformed"}})),
    ])
    herdr = Herdr("lab", run=lambda *args, **kwargs: next(replies))
    result = call(store, herdr)
    assert result["status"] == "blocked"
    assert result["reconciliation_needed"] is True
    assert result["launch_intent"]
    assert result["launch_error"] == "command_failed: prompt response malformed"
    assert result["tab_id"] == "w1:t9"
    assert result["pane_id"] == "w1:p7"
    assert result["agent_session"] == session
    assert store.read_state()["owned_panes"] == ["w1:p7"]
    assert call(StateStore(store.run_dir), object(), launch_role=lambda *a, **k: pytest.fail("relaunched")) == result


@pytest.mark.parametrize("mode", ["interactive", "oneshot"])
def test_post_tab_failure_keeps_known_ids_without_inventing_session(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    replies = iter([
        subprocess.CompletedProcess([], 0, json.dumps({"result": {"tab": {"tab_id": "w1:t9"}, "root_pane": {"pane_id": "w1:p7"}}}), ""),
        subprocess.CompletedProcess([], 1, "", json.dumps({"error": {"code": "command_failed", "message": "start failed"}})),
    ])
    def invoke(argv, **kwargs):
        # New read-only probes must not consume the failed launch response.
        if argv[3:5] == ["pane", "get"]:
            return subprocess.CompletedProcess([], 0, json.dumps({"result": {"pane": {
                "pane_id": "w1:p7", "tab_id": "w1:t9", "terminal_id": "term"}}}), "")
        if argv[3:5] == ["pane", "process-info"]:
            return subprocess.CompletedProcess([], 0, json.dumps({"result": {"process_info": {
                "pane_id": "w1:p7", "shell_pid": 123}}}), "")
        return next(replies)
    herdr = Herdr("lab", run=invoke)
    role = Role(cli="opencode", herdr_kind="opencode", model="vendor/model", effort="low", args=["{prompt}"], mode=mode, readonly=False, skills=[], axis=[])
    result = call(store, herdr, role=role)
    assert result["status"] == "blocked"
    assert result["reconciliation_needed"] is True
    assert result["launch_error"] == "command_failed: start failed"
    assert result["tab_id"] == "w1:t9"
    assert result["pane_id"] == "w1:p7"
    assert result["agent_session"] is None
    assert store.read_state()["owned_panes"] == ["w1:p7"]


@pytest.mark.parametrize(("result", "expected_tab", "expected_pane"), [
    ({"tab": {"tab_id": "w1:t9"}}, "w1:t9", None),
    ({"root_pane": {"pane_id": "w1:p7"}}, None, "w1:p7"),
    ({"tab": {"tab_id": ""}, "root_pane": {"pane_id": "w1:p7"}}, None, "w1:p7"),
    ({"tab": {"tab_id": None}, "root_pane": {"pane_id": None}}, None, None),
])
def test_incomplete_tab_create_keeps_only_known_identity(tmp_path, monkeypatch, result, expected_tab, expected_pane):
    monkeypatch.setenv("CREW_ROLE", "orchestrator")
    store = setup(tmp_path)
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess([], 0, json.dumps({"result": result}), "")
    herdr = Herdr("lab", run=run)

    task = call(store, herdr)

    assert task["status"] == "blocked"
    assert task["reconciliation_needed"] is True
    assert task["launch_intent"]
    assert task["launch_error"] == "invalid_json: Herdr tab or pane ID is missing"
    assert task.get("tab_id") == expected_tab
    assert task.get("pane_id") == expected_pane
    assert "agent_session" not in task
    assert store.read_state()["owned_panes"] == ([expected_pane] if expected_pane else [])
    assert len(calls) == 1
    assert call(StateStore(store.run_dir), object(), launch_role=lambda *a, **k: pytest.fail("relaunched")) == task
