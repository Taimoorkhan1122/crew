"""Reconcile one launched task against live agent and configured output evidence."""

from __future__ import annotations

import os
from pathlib import Path

from crew.config import Team
from crew.herdr import Herdr
from crew.state import StateStore


def _valid_session(session: object) -> bool:
    return (isinstance(session, dict)
            and isinstance(session.get("kind"), str)
            and session["kind"] in {"id", "path"}
            and all(isinstance(session.get(key), str) and session[key].strip()
                    for key in ("source", "agent", "value")))


def tick(store: StateStore, task_id: str, *, herdr: Herdr, team: Team) -> dict:
    """Observe outside the state lock; commit only against the same launch identity."""
    if os.environ.get("CREW_ROLE") != "orchestrator":
        raise PermissionError("CREW_ROLE=orchestrator required")

    state = store.read_state()
    task = state["tasks"][task_id]
    observed_status = task["status"]
    if observed_status in {"queued", "done", "failed", "paused"}:
        return dict(task)

    identity = tuple(task.get(key) for key in ("launch_intent", "tab_id", "pane_id", "agent_session"))
    pane = task.get("pane_id")
    mode = task.get("mode")
    reason = None
    agent = None
    timestamp = task.get("launch_started_at_ns")
    if not task.get("launch_intent") or type(timestamp) is not int or timestamp <= 0 or not task.get("tab_id") or not pane or pane not in state.get("owned_panes", []):
        reason = "incomplete launch identity or ownership"
    elif mode == "oneshot":
        reason = "oneshot exit status is unverified"
    elif mode != "interactive" or not _valid_session(task.get("agent_session")):
        reason = "native agent session is unavailable"
    else:
        try:
            agent = herdr.get_agent(pane)
        except Exception as error:
            reason = str(error)
        else:
            if agent.get("pane_id") != pane or not _valid_session(agent.get("agent_session")) or agent.get("agent_session") != task["agent_session"]:
                reason = "live agent identity does not match launch"
            elif agent.get("agent_status") not in {"idle", "done"}:
                if agent.get("agent_status") not in {"working"}:
                    reason = "live agent status is unverified"

    complete = False
    if reason is None and agent and agent.get("agent_status") in {"idle", "done"}:
        handoff = team.handoffs.get(task.get("role", ""))
        if handoff is None:
            reason = "configured output is unavailable"
        else:
            output = Path(handoff.out)
            if not output.is_absolute():
                output = store.run_dir / output
            try:
                with output.open(encoding="utf-8") as file:
                    before = os.fstat(file.fileno())
                    lines = file.read().splitlines()
                    after = os.fstat(file.fileno())
                    current = output.stat()
                stable = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                same_file = (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns) == (current.st_dev, current.st_ino, current.st_mtime_ns, current.st_ctime_ns)
                complete = stable and same_file and after.st_mtime_ns >= timestamp and team.done_marker in lines and any(line.strip() and line != team.done_marker for line in lines)
            except (OSError, UnicodeError):
                pass

    def commit(current):
        item = current["tasks"][task_id]
        if item["status"] in {"done", "failed", "paused"}:
            return current
        if item["status"] != observed_status:
            return current
        if tuple(item.get(key) for key in ("launch_intent", "tab_id", "pane_id", "agent_session")) != identity:
            return current
        if reason:
            item["status"] = "blocked"
            item["reconciliation_needed"] = True
            item["reconciliation_error"] = reason
        elif complete:
            item["status"] = "done"
            item.pop("reconciliation_needed", None)
            item.pop("reconciliation_error", None)
        return current

    return dict(store.update_state(commit)["tasks"][task_id])
