"""Durable, one-attempt task dispatch. Reconciliation is a separate operation."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from uuid import uuid4

from crew.config import AxisTool, Resources, Role
from crew.herdr import Herdr
from crew.launch import LaunchResult, launch
from crew.resources import ResourceReport, check_resources
from crew.state import StateStore


def _uses_capacity(item: dict) -> bool:
    return item.get("status") not in {"done", "failed", "paused"} and (item.get("status") == "running" or "launch_intent" in item)


def dispatch(
    store: StateStore,
    task_id: str,
    *,
    herdr: Herdr,
    workspace: str,
    name: str,
    role_name: str | None = None,
    cwd: str,
    role: Role,
    prompt: str,
    limits: Resources,
    axis: Mapping[str, AxisTool] | None = None,
    resource_check: Callable[..., ResourceReport] = check_resources,
    launch_role: Callable[..., LaunchResult] = launch,
) -> dict:
    """Reserve one launch attempt durably before calling Herdr outside the lock."""
    if os.environ.get("CREW_ROLE") != "orchestrator":
        raise PermissionError("CREW_ROLE=orchestrator required")

    state = store.read_state()
    task = state["tasks"][task_id]
    if task["status"] not in {"queued", "running", "done", "failed", "blocked", "paused"}:
        raise ValueError(f"unknown task status {task['status']!r}")
    if "launch_intent" in task:
        if task["status"] == "queued" and not task.get("reconciliation_needed"):
            def mark_pending(current):
                current["tasks"][task_id]["reconciliation_needed"] = True
                return current
            state = store.update_state(mark_pending)
            task = state["tasks"][task_id]
        return dict(task)
    if task["status"] != "queued":
        return dict(task)

    active = sum(
        _uses_capacity(item)
        for item in state["tasks"].values()
    )
    try:
        report = resource_check(limits, role.model, active_workers=active)
        ready = report.ready
    except Exception:
        ready = False
    if not ready:
        return dict(task)

    intent = uuid4().hex
    reserved = False
    def reserve(current):
        nonlocal reserved
        item = current["tasks"][task_id]
        current_active = sum(
            _uses_capacity(other)
            for other in current["tasks"].values()
        )
        if item["status"] == "queued" and "launch_intent" not in item and current_active < limits.max_workers:
            item["launch_intent"] = intent
            item["launch_started_at_ns"] = time.time_ns()
            item["mode"] = role.mode
            item["role"] = role_name or name
            reserved = True
        return current
    state = store.update_state(reserve)
    if not reserved:
        task = state["tasks"][task_id]
        if "launch_intent" in task and task["status"] == "queued":
            def mark_pending(current):
                current["tasks"][task_id]["reconciliation_needed"] = True
                return current
            state = store.update_state(mark_pending)
            task = state["tasks"][task_id]
        return dict(task)

    owned_before = set(getattr(herdr, "owned_panes", ()))
    result = None
    error = None
    try:
        result = launch_role(
            herdr, workspace=workspace, name=name, cwd=cwd,
            role=role, prompt=prompt, axis=axis,
        )
    except Exception as caught:
        error = str(caught)
        result = getattr(caught, "launch_result", None)

    def finish(current):
        item = current["tasks"][task_id]
        # The intent remains even if Herdr failed before returning an identity.
        if result is not None:
            if result.tab_id:
                item["tab_id"] = result.tab_id
            if result.pane_id:
                item["pane_id"] = result.pane_id
            if result.agent_session is not None or (result.tab_id and result.pane_id):
                item["agent_session"] = result.agent_session
            if result.process_identity is not None:
                item["process_identity"] = result.process_identity
            if result.output:
                item["output"] = result.output
        panes = set(current.get("owned_panes", []))
        panes.update(set(getattr(herdr, "owned_panes", ())) - owned_before)
        if result is not None and result.pane_id:
            panes.add(result.pane_id)
        current["owned_panes"] = sorted(panes)
        if error is not None or result is None or result.status not in {"started", "idle", "working", "done"}:
            item["status"] = "blocked"
            item["reconciliation_needed"] = True
            if error is not None:
                item["launch_error"] = error
        else:
            # Agent idle/done is not task completion; later tick must verify output.
            item["status"] = "running"
            item.pop("reconciliation_needed", None)
        return current
    return dict(store.update_state(finish)["tasks"][task_id])
