"""Conservative run controls; external cleanup requires durable ownership evidence."""

from __future__ import annotations

import os
from uuid import uuid4
from crew.state import StateStore
from crew.tick import _valid_session


def _require_orchestrator():
    if os.environ.get("CREW_ROLE") != "orchestrator":
        raise PermissionError("CREW_ROLE=orchestrator required")


def pause(store: StateStore, task_id: str) -> dict:
    _require_orchestrator()
    def update(state):
        task = state["tasks"][task_id]
        if task["status"] == "queued" and "launch_intent" not in task:
            task["status"] = "paused"
        elif task["status"] in {"queued", "running", "blocked"}:
            task["pause_requested"] = True
            if "launch_intent" in task:
                task["reconciliation_needed"] = True
        return state
    return dict(store.update_state(update)["tasks"][task_id])


def resume(store: StateStore, task_id: str) -> dict:
    _require_orchestrator()
    def update(state):
        task = state["tasks"][task_id]
        if task.get("stop_requested"):
            return state
        task.pop("pause_requested", None)
        if task["status"] == "paused" and "launch_intent" not in task:
            task["status"] = "queued"
        return state
    return dict(store.update_state(update)["tasks"][task_id])


def stop(store: StateStore, task_id: str) -> dict:
    _require_orchestrator()
    def update(state):
        task = state["tasks"][task_id]
        if task["status"] in {"queued", "paused"} and "launch_intent" not in task:
            task["status"] = "failed"
        elif task["status"] not in {"done", "failed"}:
            task["stop_requested"] = True
            task["reconciliation_needed"] = True
        return state
    return dict(store.update_state(update)["tasks"][task_id])


def close(store: StateStore, task_id: str | None, *, herdr, read_receipt=None) -> dict:
    """Close a settled exact-owned pane once; ambiguous outcomes stay retained."""
    _require_orchestrator()
    def record(state):
        return state['orchestrator'] if task_id is None else state['tasks'][task_id]

    def shared(state, task, pane):
        resources = list(state['tasks'].values()) + ([state['orchestrator']] if state.get('orchestrator') else [])
        return any(other is not task and other.get('pane_id') == pane for other in resources)

    def eligible(task):
        return (task.get('status') in {'idle', 'done'} if task_id is None
                else task.get('status') == 'done' or task.get('mode') == 'oneshot' and task.get('status') == 'failed')

    claimed = False
    def claim(state):
        nonlocal claimed
        task = record(state)
        if task.get("closed") or task.get("close_attempted") or task.get("close_intent") or task.get("close_identity_changed"):
            return state
        pane = task.get("pane_id")
        if pane and pane == os.environ.get('HERDR_PANE_ID'):
            task['close_error'] = 'executing caller retained'
            return state
        if (not eligible(task) or task.get("mode") not in {"interactive", "oneshot"}
                or not task.get("launch_intent") or not task.get("tab_id")
                or not isinstance(pane, str) or not pane
                or pane not in state.get("owned_panes", [])
                or (task.get('mode') == 'interactive' and not _valid_session(task.get('agent_session')))
                or (task.get('mode') == 'oneshot' and (not task.get('process_identity') or read_receipt is None))
                or shared(state, task, pane)):
            return state
        task["close_intent"] = uuid4().hex
        claimed = True
        return state
    snapshot = store.update_state(claim)
    task = record(snapshot)
    if not claimed:
        return dict(task)
    pane = task["pane_id"]
    identity_keys = ("launch_intent", "tab_id", "pane_id", "agent_session", "mode", "process_identity", "exit_code")
    identity = tuple(task.get(key) for key in identity_keys)
    intent = task["close_intent"]
    try:
        if task.get('mode') == 'oneshot':
            receipt = read_receipt()
            live = herdr.get_pane(pane)
            process = herdr.process_info(pane)
            owned = task['process_identity']
            shell = owned.get('shell_pid')
            matched = (receipt == {'launch_intent': task['launch_intent'], 'exit_code': task.get('exit_code')}
                       and type(task.get('exit_code')) is int
                       and live.get('pane_id') == pane and live.get('tab_id') == task['tab_id']
                       and live.get('terminal_id') == owned.get('terminal_id')
                       and isinstance(owned.get('terminal_id'), str) and bool(owned['terminal_id'])
                       and type(shell) is int and shell > 0
                       and process.get('pane_id') == pane and process.get('shell_pid') == shell
                       and process.get('foreground_process_group_id') == shell
                       and isinstance(process.get('foreground_processes'), list)
                       and len(process['foreground_processes']) == 1
                       and process['foreground_processes'][0].get('pid') == shell)
        else:
            agent = herdr.get_agent(pane)
            matched = (agent.get("pane_id") == pane
                       and agent.get("agent_session") == task["agent_session"]
                       and agent.get("agent_status") in {"idle", "done"})
    except Exception:
        matched = False
    # Re-read under lock after lookup; persist attempt before the external close.
    attempted = False
    def mark_attempt(state):
        nonlocal attempted
        item = record(state)
        same_intent = item.get("close_intent") == intent
        if (not matched or not same_intent or not eligible(item)
                or item.get("closed") or item.get("close_attempted")
                or tuple(item.get(key) for key in identity_keys) != identity
                or state.get("owned_panes", []).count(pane) != 1
                or shared(state, item, pane)
                or pane == os.environ.get("HERDR_PANE_ID")):
            if tuple(item.get(key) for key in identity_keys) != identity:
                item["close_identity_changed"] = True
            if same_intent:
                item.pop("close_intent", None)
            item["reconciliation_needed"] = True
            item["close_error"] = ("live agent identity or settled status is unverified"
                                   if not matched else "close intent, task identity, status or ownership changed during lookup")
            return state
        item["close_attempted"] = True
        attempted = True
        return state
    current = store.update_state(mark_attempt)
    if not attempted:
        return dict(record(current))
    try:
        herdr.command("pane", "close", pane)
    except Exception as error:
        def failed(state):
            item = record(state)
            item["reconciliation_needed"] = True
            item["close_error"] = str(error)
            return state
        return dict(record(store.update_state(failed)))
    def finish(state):
        item = record(state)
        if (item.get('close_intent') != intent
                or tuple(item.get(key) for key in identity_keys) != identity
                or state.get('owned_panes', []).count(pane) != 1 or shared(state, item, pane)):
            item['close_error'] = 'ownership changed after close; outcome uncertain'
            item['reconciliation_needed'] = True
            return state
        item["closed"] = True
        item.pop("close_intent", None)
        item.pop("close_error", None)
        state["owned_panes"] = [owned for owned in state["owned_panes"] if owned != pane]
        return state
    return dict(record(store.update_state(finish)))
