"""Small, session-scoped Herdr CLI boundary. No server lifecycle ownership."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Callable


class HerdrError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class PromptOutcome:
    status: str
    agent: dict
    output: str = ""


class Herdr:
    def __init__(self, session: str, *, run: Callable = subprocess.run, binary: str = "herdr"):
        if not session:
            raise ValueError("Herdr session is required")
        self.session = session
        self.run = run
        self.binary = binary
        self.owned_panes: set[str] = set()

    def _invoke(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return self.run(
            [self.binary, "--session", self.session, *args],
            capture_output=True,
            text=True,
            env={**os.environ, "HERDR_SESSION": self.session, **(env or {})},
            check=False,
        )

    @staticmethod
    def _payload(raw: str) -> dict:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as error:
            raise HerdrError("invalid_json", "Herdr did not return JSON") from error
        if not isinstance(payload, dict):
            raise HerdrError("invalid_json", "Herdr returned a non-object")
        return payload

    @staticmethod
    def _raise_error(payload: dict) -> None:
        detail = payload.get("error", {})
        if not isinstance(detail, dict):
            detail = {}
        raise HerdrError(str(detail.get("code", "command_failed")), str(detail.get("message", "Herdr command failed")))

    def command(self, *args: str, env: dict[str, str] | None = None) -> dict:
        process = self._invoke(*args, env=env)
        payload = self._payload(process.stdout if process.returncode == 0 else process.stderr or process.stdout)
        if process.returncode != 0 or "error" in payload:
            self._raise_error(payload)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise HerdrError("invalid_json", "Herdr result is missing")
        return result

    def create_tab(self, workspace: str, cwd: str, label: str, *, env: dict[str, str] | None = None) -> tuple[str, str]:
        args = ["tab", "create", "--workspace", workspace, "--cwd", cwd, "--label", label]
        for key, value in (env or {}).items():
            args.extend(("--env", f"{key}={value}"))
        result = self.command(*args, "--no-focus")
        tab_data = result.get("tab")
        pane_data = result.get("root_pane")
        tab = tab_data.get("tab_id") if isinstance(tab_data, dict) else None
        pane = pane_data.get("pane_id") if isinstance(pane_data, dict) else None
        tab = tab if isinstance(tab, str) and tab else None
        pane = pane if isinstance(pane, str) and pane else None
        if pane:
            self.owned_panes.add(pane)
        if not tab or not pane:
            error = HerdrError("invalid_json", "Herdr tab or pane ID is missing")
            error.tab_id = tab
            error.pane_id = pane
            raise error
        return tab, pane

    def start(self, name: str, kind: str, pane: str, args: list[str], *, timeout_ms: int) -> dict:
        result = self.command("agent", "start", name, "--kind", kind, "--pane", pane, "--timeout", str(timeout_ms), "--", *args)
        return self._agent(result)

    def get_agent(self, pane: str) -> dict:
        return self._agent(self.command("agent", "get", pane))

    @staticmethod
    def _agent(result: dict) -> dict:
        agent = result.get("agent")
        if not isinstance(agent, dict) or not isinstance(agent.get("pane_id"), str):
            raise HerdrError("invalid_json", "Herdr agent is missing")
        return agent

    def get_pane(self, pane: str) -> dict:
        value = self.command("pane", "get", pane).get("pane")
        if not isinstance(value, dict) or value.get("pane_id") != pane:
            raise HerdrError("invalid_json", "pane identity unavailable")
        return value

    def process_info(self, pane: str) -> dict:
        value = self.command("pane", "process-info", "--pane", pane).get("process_info")
        if not isinstance(value, dict) or value.get("pane_id") != pane:
            raise HerdrError("invalid_json", "pane process identity unavailable")
        return value

    def read_agent(self, pane: str) -> str:
        process = self._invoke("agent", "read", pane, "--source", "recent-unwrapped", "--lines", "120")
        if process.returncode != 0:
            self._raise_error(self._payload(process.stderr or process.stdout))
        return process.stdout

    def prompt(self, pane: str, message: str, *, timeout_ms: int) -> PromptOutcome:
        try:
            result = self.command("agent", "prompt", pane, message, "--wait", "--timeout", str(timeout_ms))
            return PromptOutcome("settled", self._agent(result))
        except HerdrError as error:
            if error.code not in {"timeout", "agent_prompt_stalled"}:
                raise
            # Submission may have succeeded. Observe only; caller decides next step.
            agent = self.get_agent(pane)
            output = self.read_agent(pane)
            return PromptOutcome("uncertain", agent, output)

    def run_pane(self, pane: str, command: str) -> None:
        self.command("pane", "run", pane, command)

    def close_owned(self, pane: str) -> None:
        if pane not in self.owned_panes:
            raise ValueError(f"pane {pane!r} is not owned")
        self.command("pane", "close", pane)
        self.owned_panes.remove(pane)
