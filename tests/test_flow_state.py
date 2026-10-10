"""Bridge behavior with real temporary StateStore files and locks."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from importlib.util import find_spec
import json
import threading
import pytest
from crew.state import StateStore
from test_flow import config

FIRST = 'T1:qa:s0:r0:role0'
DECISION = 'T1:qa:s0:r0:decision'


def bridge(store, c=None, evidence=None, expected=None):
    assert find_spec('crew.flow_state') is not None, 'locked flow persistence bridge is missing'
    from crew.flow_state import persist_next_tasks
    return persist_next_tasks(store, c or config(), 'qa', {} if evidence is None else evidence,
        item_id='T1', expected_snapshot=store.read_state() if expected is None else expected)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('CREW_ROLE', 'orchestrator')
    value = StateStore(tmp_path / 'run')
    value.write_state({'tasks': {}, 'decisions': {}, 'run_id': 'isolated',
                      'unknown': {'keep': [1, True, None]}, 'owned_panes': []})
    return value


def test_queue_ordered_role_preserves_state_and_inputs(store):
    before = store.read_state()
    c, evidence = config(), {}
    inputs = deepcopy((before, evidence, c.model_dump()))
    result = bridge(store, c, evidence, before)
    assert result.status == 'ready' and result.tasks[0].id == FIRST
    after = store.read_state()
    assert after['tasks'] == {FIRST: {'status': 'queued', 'team': 'validation',
                                    'role': 'reporter', 'step': 0, 'round': 0}}
    assert {k: v for k, v in after.items() if k != 'tasks'} == {k: v for k, v in before.items() if k != 'tasks'}
    assert (before, evidence, c.model_dump()) == inputs
    assert not store.events_path.exists()


def test_repeat_and_stale_repeat_never_rewrite_work(store):
    expected = store.read_state()
    bridge(store, expected=expected)
    inode, current = store.state_path.stat().st_ino, store.read_state()
    assert bridge(store).status == 'waiting'
    assert bridge(store, expected=expected).status == 'blocked'
    assert store.read_state() == current
    assert store.state_path.stat().st_ino == inode


@pytest.mark.parametrize('decision', [False, True])
def test_concurrent_calls_commit_exactly_once(store, decision):
    c = config([{'team': 'publish', 'decision': 'user'}]) if decision else config()
    expected = store.read_state()
    start = threading.Barrier(8)
    def call(_):
        start.wait(timeout=5)
        return bridge(store, c, expected=expected).status
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(call, range(8)))
    assert statuses.count('user_decision' if decision else 'ready') == 1
    assert statuses.count('blocked') == 7
    if decision:
        assert store.read_state()['decisions'] == {DECISION: {'status': 'pending'}}
        assert not store.read_state()['tasks']
    else:
        assert list(store.read_state()['tasks']) == [FIRST]


def test_pending_request_exact_identity_no_rewrite(store):
    c = config([{'team': 'publish', 'decision': 'user', 'action': 'push'}])
    result = bridge(store, c)
    assert result.status == 'user_decision' and result.decision_id == DECISION
    assert store.read_state()['decisions'] == {DECISION: {'status': 'pending'}}
    inode = store.state_path.stat().st_ino
    assert bridge(store, c).status == 'user_decision'
    assert store.state_path.stat().st_ino == inode


@pytest.mark.parametrize('status', ['approved', 'pending', 'rejected'])
def test_decision_never_overwritten(store, status):
    c = config([{'team': 'publish', 'decision': 'user'}])
    decision = {'status': status, 'actor': 'user', 'unknown': [True]}
    store.update_state(lambda s: {**s, 'decisions': {DECISION: decision}})
    result = bridge(store, c)
    assert store.read_state()['decisions'][DECISION] == decision
    assert result.status == ('ready' if status == 'approved' else 'user_decision')
    assert len(store.read_state()['tasks']) == (1 if status == 'approved' else 0)


@pytest.mark.parametrize('mutation', ['decision', 'task', 'control', 'metadata', 'typed_metadata'])
def test_guard_revalidated_after_lock_held_mutation(store, mutation):
    expected = store.read_state()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with store._lock():
            current = store._read_unlocked()
            if mutation == 'decision':
                current['decisions'][DECISION] = {'status': 'approved'}
            elif mutation == 'task':
                current['tasks'][FIRST] = {'status': 'queued', 'owner': 'other'}
            elif mutation == 'control':
                current['stop_requested'] = True
            elif mutation == 'metadata':
                current['run_id'] = 'replacement'
            else:
                current['unknown']['keep'][0] = True
            started = threading.Event()
            def call():
                started.set()
                return bridge(store, expected=expected)
            future = pool.submit(call)
            assert started.wait(2) and not future.done()
            store._write_unlocked(current)
        assert future.result(timeout=5).status == 'blocked'
        assert store.read_state() == current
    finally:
        pool.shutdown()


@pytest.mark.parametrize('role', [None, 'lead', 'worker', 'Orchestrator', ''])
def test_permission_before_store_io(store, monkeypatch, role):
    if role is None:
        monkeypatch.delenv('CREW_ROLE', raising=False)
    else:
        monkeypatch.setenv('CREW_ROLE', role)
    before = store.state_path.read_bytes()
    with pytest.raises(PermissionError, match='CREW_ROLE=orchestrator required'):
        bridge(store, expected={'tasks': {}})
    assert store.state_path.read_bytes() == before


@pytest.mark.parametrize('bad', [[], None, {'tasks': []}, {'tasks': {}, 'decisions': []},
    {'tasks': {'x': None}}, {'tasks': {'x': {'status': True}}},
    {'tasks': {}, 'decisions': {'x': {'status': 'unknown'}}},
    {'tasks': {}, 'pause_requested': 'false'}, {'tasks': {}, 'stop_requested': []},
    {'tasks': {}, 'number': float('nan')}])
def test_malformed_state_no_write(store, bad):
    store.state_path.write_text(json.dumps(bad))
    before = store.state_path.read_bytes()
    assert bridge(store, expected={'tasks': {}}).status == 'blocked'
    assert store.state_path.read_bytes() == before


@pytest.mark.parametrize('bad', [[], 'snapshot', {'tasks': {}, 'unknown': object()},
    {'tasks': {}, 1: 'nonstring'}, {'tasks': {}, 'number': float('inf')},
    {'tasks': {}, 'unknown': (1, 2)}])
def test_malformed_guard_no_write(store, bad):
    before = store.state_path.read_bytes()
    assert bridge(store, expected=bad).status == 'blocked'
    assert store.state_path.read_bytes() == before


@pytest.mark.parametrize('flag,value', [('pause_requested', True), ('stop_requested', True),
    ('reconciliation_needed', True), ('closed', True), ('close_attempted', True), ('close_intent', 'in-flight')])
@pytest.mark.parametrize('scope', ['run', 'task'])
def test_control_intents_preserved_and_block(store, flag, value, scope):
    state, evidence = store.read_state(), {}
    if scope == 'run':
        state[flag] = value
    else:
        state['tasks'][FIRST] = {'status': 'done', flag: value}
        evidence[FIRST] = 'Result: CONFIRMED'
    store.write_state(state)
    before = store.state_path.read_bytes()
    assert bridge(store, evidence=evidence).status == 'blocked'
    assert store.state_path.read_bytes() == before


@pytest.mark.parametrize('evidence', [[], {FIRST: ''}, {FIRST: 42}, {FIRST: 'Result: CONFIRMED\nResult: CONFIRMED'}])
def test_uncertain_evidence_no_advance(store, evidence):
    store.update_state(lambda s: {**s, 'tasks': {FIRST: {'status': 'done'}}})
    before = store.state_path.read_bytes()
    assert bridge(store, evidence=evidence).status == 'blocked'
    assert store.state_path.read_bytes() == before


def test_orphan_and_future_decision_cannot_create_work(store):
    before = store.state_path.read_bytes()
    assert bridge(store, evidence={FIRST: 'Result: CONFIRMED'}).status == 'blocked'
    assert store.state_path.read_bytes() == before
    store.update_state(lambda s: {**s, 'decisions': {'T1:qa:s3:r1:decision': {'status': 'approved'}}})
    before = store.state_path.read_bytes()
    assert bridge(store).status == 'blocked'
    assert store.state_path.read_bytes() == before


def test_role_order_rounds_exhaustion_no_reset(store):
    c = config([{'team': 'dev'}, {'team': 'review', 'gate': 'review.verdict == APPROVED',
                                  'loop': {'max_fail_rounds': 1}}])
    evidence, identities = {}, []
    for round_number in (0, 1):
        for step, role in [(0, 'driver'), (0, 'navigator'), (1, 'reviewer'), (1, 'judge')]:
            result = bridge(store, c, evidence)
            assert result.status == 'ready'
            task = result.tasks[0]
            assert (task.step, task.round, task.role) == (step, round_number, role)
            identities.append(task.id)
            def done(s):
                s['tasks'][task.id]['status'] = 'done'
                s['tasks'][task.id]['output'] = 'retained-metadata'
                return s
            store.update_state(done)
            evidence[task.id] = 'Verdict: REJECTED' if role == 'judge' else 'artifact'
    before = store.state_path.read_bytes()
    assert bridge(store, c, evidence).status == 'escalation'
    assert bridge(store, c, evidence).status == 'escalation'
    assert store.state_path.read_bytes() == before
    assert len(identities) == len(set(identities)) == 8


def test_completion_and_metadata_preservation(store):
    c = config([{'team': 'validation'}, {'team': 'publish', 'decision': 'user'}])
    bridge(store, c)
    store.update_state(lambda s: {**s, 'tasks': {FIRST: {'status': 'done', 'custom': 'retain'}}})
    evidence = {FIRST: 'artifact'}
    result = bridge(store, c, evidence)
    assert result.decision_id == 'T1:qa:s1:r0:decision'
    def approve(s):
        s['decisions'][result.decision_id] = {'status': 'approved', 'human': 'caller'}
        return s
    store.update_state(approve)
    task = bridge(store, c, evidence).tasks[0]
    def done(s):
        s['tasks'][task.id]['status'] = 'done'
        return s
    store.update_state(done)
    evidence[task.id] = 'artifact'
    before = store.state_path.read_bytes()
    assert bridge(store, c, evidence).status == 'complete'
    assert store.state_path.read_bytes() == before
    assert store.read_state()['tasks'][FIRST]['custom'] == 'retain'


def test_unrelated_lane_preserved(store):
    def prepare(s):
        s['tasks']['T2:qa:s0:r0:role0'] = {'status': 'paused', 'stop_requested': True, 'custom': {'n': 3}}
        s['decisions']['T2:qa:s3:r0:decision'] = {'status': 'approved', 'actor': 'human'}
        return s
    store.update_state(prepare)
    before = store.read_state()
    assert bridge(store).status == 'ready'
    after = store.read_state()
    assert after['tasks']['T2:qa:s0:r0:role0'] == before['tasks']['T2:qa:s0:r0:role0']
    assert after['decisions'] == before['decisions']


def test_no_external_calls(store, monkeypatch):
    import subprocess
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError('external call forbidden')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    assert bridge(store).status == 'ready'


def test_duplicate_json_keys_fail_closed(store):
    store.state_path.write_text('{"tasks": {}, "tasks": {}, "decisions": {}}')
    before = store.state_path.read_bytes()
    assert bridge(store, expected={'tasks': {}, 'decisions': {}}).status == 'blocked'
    assert store.state_path.read_bytes() == before


def test_pending_decision_binds_successful_retry_round(store):
    c = config([{'team': 'validation'}, {'team': 'review', 'gate': 'review.verdict == APPROVED',
                  'loop': {'max_fail_rounds': 1}}, {'team': 'publish', 'decision': 'user'}])
    state, evidence = store.read_state(), {}
    for round_number in (0, 1):
        for step, role_index in [(0, 0), (1, 0), (1, 1)]:
            key = f'T1:qa:s{step}:r{round_number}:role{role_index}'
            state['tasks'][key] = {'status': 'done'}
            evidence[key] = ('Verdict: REJECTED' if round_number == 0 else 'Verdict: APPROVED') if (step, role_index) == (1, 1) else 'artifact'
    store.write_state(state)
    result = bridge(store, c, evidence)
    assert result.status == 'user_decision' and result.decision_id == 'T1:qa:s2:r1:decision'
    assert store.read_state()['decisions'] == {'T1:qa:s2:r1:decision': {'status': 'pending'}}
