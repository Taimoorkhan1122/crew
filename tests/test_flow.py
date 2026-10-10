"""Pure flow behavior: real configuration, snapshots and artifact text."""
from copy import deepcopy
from pathlib import Path
from importlib.util import find_spec
import pytest
import yaml
from crew.config import CrewConfig, Flow


def config(steps=None, limit=1):
    role = dict(cli='codex', herdr_kind='codex', model='configured', effort='low', args=[],
                mode='interactive', readonly=True, skills=[], axis=[])
    teams = {name: dict(roles=roles, handoffs={r: {'in': 'brief.md', 'out': r+'.md'} for r in roles},
                        done_marker='DONE', axis=[])
             for name, roles in [('validation', ['reporter']), ('dev', ['driver', 'navigator']),
                                 ('review', ['reviewer', 'judge']), ('publish', ['publisher'])]}
    return CrewConfig.model_validate(dict(herdr_session='unused',
        resources=dict(min_free_mem_gb=0, max_load_per_core=1, max_workers=2, min_quota_runway_seconds=0),
        roles={r: role for t in teams.values() for r in t['roles']}, teams=teams, axis={},
        flows={'qa': dict(steps=steps or [dict(team='validation', gate='validation.result == CONFIRMED'),
            dict(team='dev'), dict(team='review', gate='review.verdict == APPROVED', loop={'max_fail_rounds': limit}),
            dict(team='publish', decision='user')], max_fail_rounds=limit)}))


def run(c=None, state=None, evidence=None):
    assert find_spec('crew.flow') is not None, 'pure flow engine is missing'
    from crew.flow import next_tasks
    return next_tasks(c or config(), 'qa', state if state is not None else {'tasks': {}, 'decisions': {}},
                      evidence if evidence is not None else {}, item_id='T1')


def finish(c, state, evidence, text='report'):
    task = run(c, state, evidence).tasks[0]
    state['tasks'][task.id] = {'status': 'done'}
    evidence[task.id] = text
    return task


def prefix(c):
    state, evidence = {'tasks': {}, 'decisions': {}}, {}
    finish(c, state, evidence, 'Result: CONFIRMED')
    return state, evidence


def test_deterministic_role_order_and_pure_inputs():
    c = config()
    state = {'tasks': {}, 'decisions': {}}
    original = deepcopy(state)
    first = run(c, state)
    assert first.status == 'ready'
    assert [(t.id, t.team, t.role, t.step, t.round) for t in first.tasks] == [
        ('T1:qa:s0:r0:role0', 'validation', 'reporter', 0, 0)]
    assert first == run(c, state)
    assert state == original
    state, evidence = prefix(c)
    assert run(c, state, evidence).tasks[0].role == 'driver'
    finish(c, state, evidence)
    assert run(c, state, evidence).tasks[0].role == 'navigator'


@pytest.mark.parametrize('status', ['queued', 'running', 'blocked', 'paused', 'failed'])
def test_existing_tasks_never_replayed(status):
    task = run().tasks[0]
    result = run(state={'tasks': {task.id: {'status': status}}, 'decisions': {}})
    assert not result.tasks
    assert result.status == ('waiting' if status in {'queued', 'running'} else 'blocked')


@pytest.mark.parametrize('text', [None, '', 'CONFIRMED', 'Result: confirmed', 'Result: CONFIRMED extra',
    'Result: CONFIRMED\nResult: CONFIRMED', 'Result: CONFIRMED\nResult: NOT_CONFIRMED',
    'Verdict: APPROVED', 'Result: UNKNOWN', '> Result: CONFIRMED',
    '```\nResult: CONFIRMED\n```', 'Result: CONFIRMED\nVerdict: REJECTED', 42])
def test_bad_evidence_cannot_authorize(text):
    result = run(state={'tasks': {'T1:qa:s0:r0:role0': {'status': 'done'}}, 'decisions': {}},
                 evidence={'T1:qa:s0:r0:role0': text})
    assert result.status == 'blocked'
    assert not result.tasks


@pytest.mark.parametrize('gate', ['__import__("os").system("false")', 'validation.result != CONFIRMED',
    'validation.result == CONFIRMED or True', 'wrong.result == CONFIRMED', 'validation.other == CONFIRMED'])
def test_unsupported_expressions_block_before_dispatch(gate):
    result = run(config([dict(team='validation', gate=gate)]))
    assert result.status == 'blocked'
    assert not result.tasks


def test_negative_validation_stops_without_retry():
    c = config()
    state, evidence = prefix(c)
    evidence['T1:qa:s0:r0:role0'] = 'Result: NOT_CONFIRMED'
    assert run(c, state, evidence).status == 'blocked'


def test_failed_review_retry_and_exhaustion():
    c = config()
    state, evidence = prefix(c)
    for _ in range(3):
        finish(c, state, evidence)
    judge = finish(c, state, evidence, 'Verdict: REJECTED')
    retry = run(c, state, evidence)
    assert (retry.tasks[0].id, retry.tasks[0].team, retry.tasks[0].role, retry.tasks[0].round) == (
        'T1:qa:s1:r1:role0', 'dev', 'driver', 1)
    assert judge.round == 0
    for _ in range(3):
        finish(c, state, evidence)
    finish(c, state, evidence, 'Verdict: REJECTED')
    exhausted = run(c, state, evidence)
    assert exhausted.status == 'escalation'
    assert not exhausted.tasks
    assert 'max_fail_rounds' in exhausted.reason


def test_retry_success_decision_and_complete_no_replay():
    c = config()
    state, evidence = prefix(c)
    for _ in range(3):
        finish(c, state, evidence)
    finish(c, state, evidence, 'Verdict: REJECTED')
    for _ in range(3):
        finish(c, state, evidence)
    finish(c, state, evidence, 'Verdict: APPROVED')
    result = run(c, state, evidence)
    assert result.status == 'user_decision'
    assert result.decision_id == 'T1:qa:s3:r1:decision'
    state['decisions'][result.decision_id] = {'status': 'approved'}
    assert run(c, state, evidence).tasks[0].round == 1
    finish(c, state, evidence)
    result = run(c, state, evidence)
    assert result.status == 'complete' and not result.tasks


@pytest.mark.parametrize('decision', [None, {}, {'status': 'pending'}, {'status': 'rejected'},
    {'status': True}, {'status': 'APPROVED'}, 'approved'])
def test_decisions_require_explicit_approval(decision):
    c = config([dict(team='publish', decision='user')])
    result = run(c)
    result = run(c, {'tasks': {}, 'decisions': {result.decision_id: decision}})
    assert result.status in {'user_decision', 'blocked'}
    assert not result.tasks


@pytest.mark.parametrize('state', [[], {'tasks': []}, {'tasks': {}, 'decisions': []},
    {'tasks': {'T1:qa:s0:r0:role0': {'status': []}}, 'decisions': {}},
    {'tasks': {'T1:qa:s1:r0:role0': {'status': 'done'}}, 'decisions': {}}])
def test_malformed_and_out_of_order_snapshots_block(state):
    result = run(state=state)
    assert result.status == 'blocked' and not result.tasks


def test_completed_ungated_role_still_requires_artifact():
    c = config([dict(team='dev')])
    task = run(c).tasks[0]
    result = run(c, {'tasks': {task.id: {'status': 'done'}}, 'decisions': {}})
    assert result.status == 'blocked' and not result.tasks


def test_zero_budget_escalates_first_failure():
    c = config(limit=0)
    state, evidence = prefix(c)
    for _ in range(3):
        finish(c, state, evidence)
    finish(c, state, evidence, 'Verdict: REJECTED')
    assert run(c, state, evidence).status == 'escalation'


@pytest.mark.parametrize('action', ['push', 'pr', 'merge'])
def test_protected_actions_require_user_gate(action):
    c = config([dict(team='publish', action=action)])
    assert run(c).status == 'blocked'
    c.flows['qa'].steps[0].decision = 'user'
    result = run(c)
    assert run(c, {'tasks': {}, 'decisions': {result.decision_id: {'status': 'approved'}}}).status == 'ready'


def test_package_flow_examples_validate():
    root = Path(__file__).parents[1] / 'examples' / 'flows'
    for name in ['qa-bugfix', 'readonly']:
        assert (root / (name+'.yaml')).is_file()
        assert Flow.model_validate(yaml.safe_load((root / (name+'.yaml')).read_text())).steps
    qa = Flow.model_validate(yaml.safe_load((root / 'qa-bugfix.yaml').read_text()))
    assert any(step.loop for step in qa.steps)
    assert any(step.decision == 'user' for step in qa.steps)


@pytest.mark.parametrize('review_text', ['Verdict: REJECTED', 'Verdict: APPROVED extra', 'Result: CONFIRMED'])
def test_conflicting_or_malformed_earlier_team_evidence_blocks(review_text):
    c = config()
    state, evidence = prefix(c)
    finish(c, state, evidence)
    finish(c, state, evidence)
    reviewer = finish(c, state, evidence)
    finish(c, state, evidence, 'Verdict: APPROVED')
    evidence[reviewer.id] = review_text
    result = run(c, state, evidence)
    assert result.status == 'blocked' and not result.tasks


def test_orphan_artifact_does_not_replay_missing_task():
    result = run(evidence={'T1:qa:s0:r0:role0': 'Result: CONFIRMED'})
    assert result.status == 'blocked' and not result.tasks


@pytest.mark.parametrize('field,value', [('role', 'wrong'), ('team', 'wrong'), ('step', 2), ('round', 1)])
def test_conflicting_task_metadata_blocks(field, value):
    state = {'tasks': {'T1:qa:s0:r0:role0': {'status': 'done', field: value}}, 'decisions': {}}
    result = run(state=state, evidence={'T1:qa:s0:r0:role0': 'Result: CONFIRMED'})
    assert result.status == 'blocked' and not result.tasks


@pytest.mark.parametrize('team', ['push', 'pr', 'merge', 'pull-request'])
def test_reserved_action_team_cannot_omit_user_gate(team):
    c = config([dict(team='publish')])
    c.teams[team] = c.teams.pop('publish')
    c.flows['qa'].steps[0].team = team
    result = run(c)
    assert result.status == 'blocked' and not result.tasks


def test_flow_budget_caps_step_budget():
    c = config()
    c.flows['qa'].max_fail_rounds = 0
    state, evidence = prefix(c)
    for _ in range(3):
        finish(c, state, evidence)
    finish(c, state, evidence, 'Verdict: REJECTED')
    assert run(c, state, evidence).status == 'escalation'


def test_inconclusive_does_not_authorize_loop():
    c = config([dict(team='dev'), dict(team='validation', gate='validation.result == CONFIRMED',
                                     loop={'max_fail_rounds': 1})])
    state, evidence = {'tasks': {}, 'decisions': {}}, {}
    finish(c, state, evidence)
    finish(c, state, evidence)
    finish(c, state, evidence, 'Result: INCONCLUSIVE')
    assert run(c, state, evidence).status == 'blocked'


def test_later_lane_and_snapshot_inputs_remain_unchanged():
    c = config()
    state, evidence = prefix(c)
    state['tasks']['T2:qa:s99:r99:role0'] = {'status': 'done'}
    evidence['T2:qa:s99:r99:role0'] = 'Result: REJECTED'
    before = deepcopy((c.model_dump(), state, evidence))
    assert run(c, state, evidence).status == 'ready'
    assert (c.model_dump(), state, evidence) == before


@pytest.mark.parametrize('flag', ['pause_requested', 'stop_requested', 'reconciliation_needed'])
def test_completed_task_with_control_uncertainty_blocks(flag):
    c = config()
    state, evidence = prefix(c)
    state['tasks']['T1:qa:s0:r0:role0'][flag] = True
    assert run(c, state, evidence).status == 'blocked'


@pytest.mark.parametrize('change', ['empty_roles', 'duplicate_roles', 'missing_team', 'unknown_decision', 'loop_first'])
def test_invalid_config_is_rejected_before_tasks(change):
    c = config()
    if change == 'empty_roles':
        c.teams['validation'].roles = []
        c.teams['validation'].handoffs = {}
    elif change == 'duplicate_roles':
        c.teams['validation'].roles = ['reporter', 'reporter']
    elif change == 'missing_team':
        c.flows['qa'].steps[0].team = 'missing'
    elif change == 'unknown_decision':
        c.flows['qa'].steps[0].decision = 'agent'
    else:
        c.flows['qa'].steps[0].loop = c.flows['qa'].steps[2].loop
    assert run(c).status == 'blocked'
