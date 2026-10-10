"""Pure, deterministic flow planning. No IO, persistence, dispatch, or eval."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Literal

from crew.config import CrewConfig, _validate_references


@dataclass(frozen=True)
class FlowTask:
    id: str
    team: str
    role: str
    step: int
    round: int


@dataclass(frozen=True)
class FlowResult:
    status: Literal['ready', 'waiting', 'blocked', 'user_decision', 'escalation', 'complete']
    tasks: tuple[FlowTask, ...] = ()
    reason: str = ''
    decision_id: str | None = None


_VALUES = {'result': {'CONFIRMED', 'NOT_CONFIRMED', 'INCONCLUSIVE'},
           'verdict': {'APPROVED', 'REJECTED'}}
_GATE = re.compile(r'([A-Za-z_][A-Za-z0-9_-]*)\.(result|verdict) == ([A-Z_]+)')
_MARKER = re.compile(r'(result|verdict)\s*:', re.IGNORECASE)
_LINE = re.compile(r'(Result|Verdict): ([A-Z_]+)')


def _gate_value(text: str, field: str) -> str:
    """Require exactly one unquoted, unfenced, standalone canonical marker."""
    lines = [line for line in text.splitlines() if _MARKER.search(line)]
    if len(lines) != 1 or '```' in text or '~~~' in text:
        raise ValueError('missing or conflicting gate evidence')
    match = _LINE.fullmatch(lines[0])
    if not match or match[1].lower() != field or match[2] not in _VALUES[field]:
        raise ValueError('malformed gate evidence')
    return match[2]


def next_tasks(config: CrewConfig, flow_name: str, snapshot: Mapping, evidence: Mapping,
               *, item_id: str) -> FlowResult:
    """Replay one item's reachable history; propose at most one ordered role.

    Inputs and round/decision contracts are specified in docs/flow-engine.md.
    Callers supply verified task-scoped artifact text, never filesystem paths.
    """
    try:
        if not isinstance(config, CrewConfig):
            raise ValueError('validated CrewConfig required')
        # Revalidate mutable Pydantic objects, including references after overrides.
        config = CrewConfig.model_validate(config.model_dump(by_alias=True))
        _validate_references(config)
        if not isinstance(item_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', item_id):
            raise ValueError('item_id must be a nonempty ASCII identifier')
        if not isinstance(flow_name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', flow_name):
            raise ValueError('flow_name must be a nonempty ASCII identifier')
        if flow_name not in config.flows:
            raise ValueError('unknown flow')
        flow = config.flows[flow_name]
        if not flow.steps:
            raise ValueError('flow has no steps')
        gates = []
        for index, step in enumerate(flow.steps):
            team = config.teams[step.team]
            if not team.roles or len(team.roles) != len(set(team.roles)):
                raise ValueError('team must have nonempty unique ordered roles')
            if step.decision not in (None, 'user'):
                raise ValueError('only decision: user is supported')
            if (step.action is not None or step.team in ('push', 'pr', 'merge', 'pull-request')) and step.decision != 'user':
                raise ValueError('push/pr/merge requires decision: user')
            gate = _GATE.fullmatch(step.gate) if step.gate is not None else None
            if step.gate is not None and (not gate or gate[1] != step.team or gate[3] not in _VALUES[gate[2]]):
                raise ValueError('unsupported gate expression')
            if step.loop is not None:
                if gate is None or index == 0:
                    raise ValueError('loop requires a gate and a preceding repair step')
                previous = flow.steps[index - 1]
                if previous.loop or previous.decision or previous.action:
                    raise ValueError('loop cannot replay a loop or user-gated action')
            gates.append(gate)
        if not isinstance(snapshot, Mapping) or not isinstance(evidence, Mapping):
            raise ValueError('snapshot and evidence must be mappings')
        tasks, decisions = snapshot.get('tasks'), snapshot.get('decisions', {})
        if not isinstance(tasks, Mapping) or not isinstance(decisions, Mapping):
            raise ValueError('tasks and decisions must be mappings')
        if any(not isinstance(key, str) for key in (*tasks, *decisions, *evidence)):
            raise ValueError('snapshot and evidence keys must be strings')
        prefix = f'{item_id}:{flow_name}:'
        if any(key.startswith(prefix) and key not in tasks for key in evidence):
            raise ValueError('orphan artifact has no task state; reconciliation required')
        visited, asked = set(), set()
        failures = [0] * len(flow.steps)
        index, round_number = 0, 0

        def outcome(result: FlowResult) -> FlowResult:
            # A stale/future task or approval cannot skip a prerequisite.
            if any(key.startswith(prefix) and key not in visited for key in tasks):
                return FlowResult('blocked', reason='unreachable or out-of-order task state')
            if any(key.startswith(prefix) and key not in asked for key in decisions):
                return FlowResult('blocked', reason='unreachable or out-of-order user decision')
            return result

        while index < len(flow.steps):
            step = flow.steps[index]
            stem = f'{prefix}s{index}:r{round_number}:'
            if step.decision == 'user':
                decision_id = stem + 'decision'
                asked.add(decision_id)
                if decision_id not in decisions:
                    return outcome(FlowResult('user_decision', reason='explicit user approval required', decision_id=decision_id))
                decision = decisions[decision_id]
                if not isinstance(decision, Mapping) or decision.get('status') not in ('approved', 'pending', 'rejected'):
                    return outcome(FlowResult('blocked', reason='malformed user decision', decision_id=decision_id))
                if decision['status'] != 'approved':
                    return outcome(FlowResult('user_decision', reason=f"user decision is {decision['status']}", decision_id=decision_id))
            team = config.teams[step.team]
            last_text = ''
            gate = gates[index]
            observed = []
            for role_index, role in enumerate(team.roles):
                task_id = stem + f'role{role_index}'
                visited.add(task_id)
                proposed = FlowTask(task_id, step.team, role, index, round_number)
                if task_id not in tasks:
                    return outcome(FlowResult('ready', (proposed,)))
                task = tasks[task_id]
                if not isinstance(task, Mapping):
                    raise ValueError('malformed task record')
                for field, expected in (('team', step.team), ('role', role), ('step', index), ('round', round_number)):
                    if field in task and (type(task[field]) is not type(expected) or task[field] != expected):
                        raise ValueError('task metadata conflicts with deterministic identity')
                status = task.get('status')
                if not isinstance(status, str) or status not in ('queued', 'running', 'done', 'failed', 'blocked', 'paused'):
                    raise ValueError('malformed task status')
                if task.get('reconciliation_needed') or task.get('pause_requested') or task.get('stop_requested'):
                    return outcome(FlowResult('blocked', reason='task requires reconciliation or is paused/stopped'))
                if status != 'done':
                    return outcome(FlowResult('waiting' if status in ('queued', 'running') else 'blocked', reason=f'task is {status}'))
                last_text = evidence.get(task_id)
                if not isinstance(last_text, str) or not last_text.strip():
                    raise ValueError('completed task requires nonempty artifact evidence')
                if gate is not None and _MARKER.search(last_text):
                    observed.append(_gate_value(last_text, gate[2]))
                    if len(set(observed)) != 1:
                        raise ValueError('conflicting team gate evidence')
            if gate is not None:
                value = _gate_value(last_text, gate[2])
                if value != gate[3]:
                    # Inconclusive evidence is never a failed review authorization.
                    if value == 'INCONCLUSIVE':
                        return outcome(FlowResult('blocked', reason='gate evidence is inconclusive'))
                    if step.loop is None:
                        return outcome(FlowResult('blocked', reason='gate did not pass'))
                    if failures[index] >= step.loop.max_fail_rounds or round_number >= flow.max_fail_rounds:
                        return outcome(FlowResult('escalation', reason='max_fail_rounds exhausted; user decision required'))
                    failures[index] += 1
                    round_number += 1
                    index -= 1
                    continue
            index += 1
        return outcome(FlowResult('complete'))
    except (ValueError, TypeError, KeyError) as error:
        return FlowResult('blocked', reason=str(error))
