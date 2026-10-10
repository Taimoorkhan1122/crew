"""Locked persistence for pure flow proposals; no dispatch or user decisions."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import os

from crew.config import CrewConfig
from crew.flow import FlowResult, next_tasks
from crew.state import StateStore


_FLAGS = ('pause_requested', 'stop_requested', 'reconciliation_needed', 'closed', 'close_attempted')
_STATUSES = ('queued', 'running', 'done', 'failed', 'blocked', 'paused')


def _json(value):
    """Freeze JSON input and keep type distinctions that Python equality loses."""
    def validate(item):
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError('JSON object keys must be strings')
            for child in item.values():
                validate(child)
        elif type(item) is list:
            for child in item:
                validate(child)
        elif type(item) is float:
            if not math.isfinite(item):
                raise ValueError('nonfinite JSON number')
        elif item is not None and type(item) not in (str, int, bool):
            raise ValueError('plain JSON values required')
    validate(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON object key')
        result[key] = value
    return result


def _control(record):
    active = False
    for flag in _FLAGS:
        if flag in record:
            if type(record[flag]) is not bool:
                raise ValueError('malformed control flag')
            active |= record[flag]
    if 'close_intent' in record:
        if type(record['close_intent']) is not str or not record['close_intent']:
            raise ValueError('malformed close intent')
        active = True
    return active


def _state(state, prefix):
    if type(state) is not dict or type(state.get('tasks')) is not dict:
        raise ValueError('state requires a tasks object')
    if type(state.get('decisions', {})) is not dict:
        raise ValueError('decisions must be an object')
    active = _control(state)
    for task_id, task in state['tasks'].items():
        if type(task) is not dict or type(task.get('status')) is not str or task['status'] not in _STATUSES:
            raise ValueError('malformed task state')
        controlled = _control(task)
        if task_id.startswith(prefix):
            active |= controlled
    for decision in state.get('decisions', {}).values():
        if (type(decision) is not dict or type(decision.get('status')) is not str
                or decision['status'] not in ('approved', 'pending', 'rejected')):
            raise ValueError('malformed decision state')
    return active


def persist_next_tasks(store: StateStore, config: CrewConfig, flow_name: str, evidence: dict,
                       *, item_id: str, expected_snapshot: dict) -> FlowResult:
    """Compare an exact authoritative snapshot under lock, then persist one proposal.

    The caller verifies task-scoped evidence ownership/freshness against the
    supplied snapshot. A stale guard returns blocked; obtain fresh evidence and
    a fresh snapshot before retrying. No decision status is changed here.
    """
    if os.environ.get('CREW_ROLE') != 'orchestrator':
        raise PermissionError('CREW_ROLE=orchestrator required')
    try:
        if not isinstance(store, StateStore):
            raise ValueError('StateStore required')
        guard = _json(expected_snapshot)
        prefix = f'{item_id}:{flow_name}:'
        _state(expected_snapshot, prefix)
        evidence = json.loads(_json(evidence))
        if type(evidence) is not dict or any(type(text) is not str or not text.strip() for text in evidence.values()):
            raise ValueError('task-scoped nonempty evidence text required')
        config = deepcopy(config)
        # ponytail: whole-run lock and snapshot; narrower guards only if contention warrants it.
        with store._lock():
            # Strict decoding rejects ambiguity that ordinary json.load discards.
            with store.state_path.open(encoding='utf-8') as file:
                current = json.load(file, object_pairs_hook=_object)
            encoded = _json(current)
            if encoded != guard:
                return FlowResult('blocked', reason='authoritative snapshot changed; refresh snapshot and verified evidence')
            if _state(current, prefix):
                return FlowResult('blocked', reason='control intent or reconciliation uncertainty blocks flow persistence')
            result = next_tasks(config, flow_name, current, evidence, item_id=item_id)
            if result.status == 'ready':
                task, = result.tasks
                current['tasks'][task.id] = {'status': 'queued', 'team': task.team,
                    'role': task.role, 'step': task.step, 'round': task.round}
                store._write_unlocked(current)
            elif result.status == 'user_decision' and result.decision_id not in current.get('decisions', {}):
                current.setdefault('decisions', {})[result.decision_id] = {'status': 'pending'}
                store._write_unlocked(current)
            return result
    except (ValueError, TypeError, KeyError, OSError, RecursionError) as error:
        return FlowResult('blocked', reason=str(error))
