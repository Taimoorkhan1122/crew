import json
import threading
from pathlib import Path

import pytest

from crew.state import StateStore


def test_state_round_trip_update_and_event_log(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "run")
    store.write_state({"tasks": {"A": {"status": "queued"}}, "count": 0})
    updated = store.update_state(lambda state: {**state, "count": state["count"] + 1})
    store.append_event({"type": "task_queued", "task": "A"})

    assert updated["count"] == 1
    assert store.read_state() == updated
    assert (tmp_path / "run" / "state.json").read_text().endswith("\n")
    assert json.loads((tmp_path / "run" / "events.jsonl").read_text().splitlines()[0]) == {
        "type": "task_queued", "task": "A",
    }


def test_state_updates_are_serialized_between_threads(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "run")
    store.write_state({"count": 0})

    def increment() -> None:
        for _ in range(20):
            store.update_state(lambda state: {**state, "count": state["count"] + 1})

    threads = [threading.Thread(target=increment) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert store.read_state()["count"] == 80


def test_failed_state_serialization_keeps_previous_file(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "run")
    store.write_state({"safe": True})

    with pytest.raises(TypeError):
        store.write_state({"unsafe": object()})

    assert store.read_state() == {"safe": True}


def test_event_log_is_private_even_with_permissive_umask(tmp_path: Path) -> None:
    import os
    import stat

    store = StateStore(tmp_path / "run")
    previous = os.umask(0)
    try:
        store.append_event({"type": "started"})
    finally:
        os.umask(previous)

    assert stat.S_IMODE(store.events_path.stat().st_mode) == 0o600


def test_event_failure_does_not_change_authoritative_state(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "run")
    store.write_state({"phase": "running"})
    store.events_path.mkdir()

    with pytest.raises(IsADirectoryError):
        store.append_event({"type": "started"})

    assert store.read_state() == {"phase": "running"}
