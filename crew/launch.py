"""Launch one configured role into a Herdr tab; scheduling belongs elsewhere."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Mapping

from crew.config import AxisTool, Role
from crew.herdr import Herdr


@dataclass(frozen=True)
class LaunchResult:
    tab_id: str | None
    pane_id: str | None
    status: str
    agent_session: dict | None = None
    output: str = ""
    process_identity: dict | None = None

    @property
    def agent_session_id(self) -> str | None:
        if self.agent_session and self.agent_session.get("kind") == "id":
            value = self.agent_session.get("value")
            return value if isinstance(value, str) else None
        return None


def launch(
    herdr: Herdr,
    *,
    workspace: str,
    name: str,
    cwd: str,
    role: Role,
    prompt: str,
    axis: Mapping[str, AxisTool] | None = None,
    timeout_ms: int = 60000,
) -> LaunchResult:
    """Create one owned pane and launch it. Ambiguous prompt delivery stays explicit."""
    values = {"model": role.model, "effort": role.effort, "prompt": prompt}
    args = [arg.format_map(values) for arg in role.args]
    gates: dict[str, str] = {}
    for axis_name in role.axis:
        tool = (axis or {})[axis_name]
        gate = tool.write_gate
        if not role.readonly or not gate:
            continue
        if gate.startswith("--"):
            if gate in args:
                raise ValueError(f"read-only role cannot pass write gate {gate}")
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*_READ_ONLY", gate):
            gates[gate] = "1"
        else:
            raise ValueError(f"unsupported read-only write gate {gate!r}")
    if role.mode == "oneshot" and "{prompt}" not in role.args:
        raise ValueError("oneshot args must contain {prompt}")

    try:
        tab_id, pane_id = herdr.create_tab(workspace, cwd, name, env=gates)
    except Exception as error:
        tab_id = getattr(error, "tab_id", None)
        pane_id = getattr(error, "pane_id", None)
        if tab_id or pane_id:
            error.launch_result = LaunchResult(tab_id, pane_id, "uncertain")
        raise
    session = None
    process_identity = None
    try:
        if role.mode == "interactive":
            started = herdr.start(name, role.herdr_kind, pane_id, args, timeout_ms=timeout_ms)
            session = started.get("agent_session")
            outcome = herdr.prompt(pane_id, prompt, timeout_ms=timeout_ms)
            agent = outcome.agent
            return LaunchResult(tab_id, pane_id, outcome.status if outcome.status == "uncertain" else agent.get("agent_status", "unknown"), agent.get("agent_session") or session, outcome.output)

        command = shlex.join(["cd", "--", cwd]) + " && " + shlex.join(["env", *(f"{key}={value}" for key, value in gates.items()), role.cli, *args]) + " < /dev/null"
        # Capture terminal/shell ownership before submitting ordinary command text.
        process_identity = None
        try:
            pane = herdr.get_pane(pane_id)
            process = herdr.process_info(pane_id)
            if (pane.get('pane_id') == pane_id and pane.get('tab_id') == tab_id
                    and isinstance(pane.get('terminal_id'), str) and pane['terminal_id']
                    and process.get('pane_id') == pane_id
                    and type(process.get('shell_pid')) is int and process['shell_pid'] > 0):
                process_identity = {'terminal_id': pane['terminal_id'], 'shell_pid': process['shell_pid']}
        except Exception:
            pass  # Missing evidence never permits later pane adoption or cleanup.
        herdr.run_pane(pane_id, command)
        return LaunchResult(tab_id, pane_id, "started", process_identity=process_identity)
    except Exception as error:
        error.launch_result = LaunchResult(tab_id, pane_id, "uncertain", session, process_identity=process_identity)
        raise
