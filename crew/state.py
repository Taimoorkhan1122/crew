from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class StateStore:
    """Store authoritative JSON state and a best-effort event audit log.

    Events are not a recovery source or transaction record; state remains
    authoritative if an event append fails.
    """

    def __init__(self, run_dir: str | Path):
        self.run_dir = Path(run_dir)
        self.state_path = self.run_dir / "state.json"
        self.events_path = self.run_dir / "events.jsonl"
        self.lock_path = self.run_dir / ".state.lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read_unlocked(self) -> dict:
        with self.state_path.open(encoding="utf-8") as file:
            state = json.load(file)
        if not isinstance(state, dict):
            raise ValueError("state.json must contain a JSON object")
        return state

    def read_state(self) -> dict:
        with self._lock():
            return self._read_unlocked()

    def _write_unlocked(self, state: Mapping) -> dict:
        if not isinstance(state, Mapping):
            raise TypeError("state must be a mapping")
        payload = json.dumps(state, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        fd, temp_name = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=self.run_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_name, self.state_path)
            dir_fd = os.open(self.run_dir, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return dict(state)

    def write_state(self, state: Mapping) -> dict:
        with self._lock():
            return self._write_unlocked(state)

    def update_state(self, update: Callable[[dict], Mapping]) -> dict:
        with self._lock():
            updated = update(self._read_unlocked())
            return self._write_unlocked(updated)

    def append_event(self, event: Mapping) -> None:
        """Append a private audit event; errors are reported to the caller."""
        line = json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        with self._lock():
            fd = os.open(self.events_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    fd = -1
                    file.write(line + "\n")
                    file.flush()
                    os.fsync(file.fileno())
            finally:
                if fd >= 0:
                    os.close(fd)
