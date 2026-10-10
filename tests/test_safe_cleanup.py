"""Real temporary Git and fake Herdr cleanup fixtures."""
from pathlib import Path
import pytest
from crew.control import close
from test_control import store_at
from test_worktrees import mutable, run, first
from test_runtime_cli import setup, make_run, FakeHerdr, ready

@pytest.mark.parametrize('fault',['none','terminal','tab','shell','foreground','receipt','ownership','intent'])
def test_oneshot_exact_completed_shell(tmp_path,monkeypatch,fault):
    monkeypatch.setenv('CREW_ROLE','orchestrator'); store=store_at(tmp_path,'done')
    store.update_state(lambda s: (s['tasks']['A'].update(mode='oneshot',exit_code=0,
        process_identity={'terminal_id':'term1','shell_pid':123}) or s))
    class Fake:
        calls=[]
        def get_pane(self,pane):
            if fault in ('ownership','intent'):
                def mutate(s):
                    if fault=='ownership': s['owned_panes']=[]
                    else: s['tasks']['A']['launch_intent']='changed'
                    return s
                store.update_state(mutate)
            return {'pane_id':pane,'tab_id':'wrong' if fault=='tab' else 'w:t1',
                'terminal_id':'wrong' if fault=='terminal' else 'term1'}
        def process_info(self,pane): return {'pane_id':pane,'shell_pid':124 if fault=='shell' else 123,
            'foreground_process_group_id':125 if fault=='foreground' else 123,
            'foreground_processes':[{'pid':125 if fault=='foreground' else 123}]}
        def command(self,*args): self.calls.append(args)
    def receipt():
        if fault=='receipt': raise FileNotFoundError()
        return {'launch_intent':'i1','exit_code':0}
    fake=Fake(); task=close(store,'A',herdr=fake,read_receipt=receipt)
    if fault=='none':
        assert task['closed']; assert fake.calls==[('pane','close','w:p1')]
        assert close(store,'A',herdr=fake,read_receipt=receipt)['closed']; assert len(fake.calls)==1
    else:
        assert not task.get('closed') and task['close_error']; assert fake.calls==[]


def test_stop_reports_active_repeat(setup,monkeypatch):
    runtime,_=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    adapter=FakeHerdr(); runtime.start(herdr=adapter); runtime.tick(herdr=adapter,resource_check=ready)
    runtime.request_control('stop')
    for _ in range(2):
        result=runtime.tick(herdr=adapter,resource_check=ready)
        assert result['cleanup']['active_tasks'] and not result['cleanup']['stopped']
    assert not adapter.closed


def test_clean_active_writer_retains_then_closed_removes(mutable):
    runtime,adapter=run(mutable); task_id,state=first(runtime,adapter)
    record=state['lanes'][0]['worktrees'][0]; path=Path(record['path'])
    runtime.request_control('close'); result=runtime.tick(herdr=adapter,resource_check=ready)
    assert path.exists() and not result['cleanup']['closed']
    assert result['cleanup']['worktrees'][0]['reason']=='active or uncertain processes retained'
    task=runtime.store.read_state()['tasks'][task_id]
    (runtime.path/task['artifact']).write_text('body\nQA DONE\n')
    result=runtime.tick(herdr=adapter,resource_check=ready)
    assert not path.exists() and result['cleanup']['worktrees'][0]['status']=='removed'
    assert result['cleanup']['retained_branches']
    assert runtime.tick(herdr=adapter,resource_check=ready)['cleanup']['worktrees'][0]['status']=='removed'

@pytest.mark.parametrize('fault',['dirty','keep','unowned','uncertain'])
def test_worktree_retention(mutable,fault):
    runtime,adapter=run(mutable); task_id,state=first(runtime,adapter)
    record=state['lanes'][0]['worktrees'][0]; path=Path(record['path'])
    task=state['tasks'][task_id]; (runtime.path/task['artifact']).write_text('body\nQA DONE\n')
    runtime.request_control('pause'); runtime.tick(herdr=adapter,resource_check=ready)
    if fault=='dirty': (path/'source.txt').write_text('changed')
    if fault in ('unowned','uncertain'):
        def mutate(s):
            if fault=='unowned': s['lanes'][0]['worktrees'][0]['path_identity']=[0,0]
            else: s['tasks'][task_id]['closed']=False; s['tasks'][task_id]['close_attempted']=True
            return s
        runtime.store.update_state(mutate)
    runtime.request_control('close',keep_worktrees=fault=='keep')
    result=runtime.tick(herdr=adapter,resource_check=ready)
    assert path.exists() and result['cleanup']['worktrees'][0]['status']=='retained'


def test_orchestrator_current_caller_retained(setup,monkeypatch):
    runtime,_=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    adapter=FakeHerdr(); runtime.start(herdr=adapter)
    session={'kind':'id','source':'integration:fake','agent':'fake','value':'orch'}
    adapter.agents['w1:p9']={'pane_id':'w1:p9','agent_session':session,'agent_status':'idle'}
    runtime.store.update_state(lambda s: {**s,'owned_panes':['w1:p9'],'orchestrator':{
        'pane_id':'w1:p9','tab_id':'w1:t1','launch_intent':'orch','mode':'interactive','status':'idle','agent_session':session}})
    monkeypatch.setenv('HERDR_PANE_ID','w1:p9')
    runtime.request_control('close'); result=runtime.tick(herdr=adapter,resource_check=ready)
    assert not adapter.closed and not result['cleanup']['closed']
    assert result['cleanup']['orchestrator']['reason']=='executing caller retained'


@pytest.mark.parametrize('fault', ['none', 'identity', 'working', 'ownership', 'intent', 'error'])
def test_orchestrator_exact_cleanup(tmp_path, monkeypatch, fault):
    from test_control import SESSION
    monkeypatch.setenv('CREW_ROLE', 'orchestrator')
    monkeypatch.delenv('HERDR_PANE_ID', raising=False)
    store = store_at(tmp_path, 'done')
    store.update_state(lambda s: {**s, 'tasks': {}, 'orchestrator': {
        'status': 'idle', 'mode': 'interactive', 'launch_intent': 'orch',
        'pane_id': 'w:p1', 'tab_id': 'w:t1', 'agent_session': SESSION}})
    class Fake:
        calls = 0
        def get_agent(self, pane):
            if fault in ('ownership', 'intent'):
                def mutate(s):
                    if fault == 'ownership': s['owned_panes'] = []
                    else: s['orchestrator']['launch_intent'] = 'changed'
                    return s
                store.update_state(mutate)
            return {'pane_id': pane, 'agent_session': {**SESSION, 'value': 'other'} if fault == 'identity' else SESSION,
                    'agent_status': 'working' if fault == 'working' else 'idle'}
        def command(self, *args):
            self.calls += 1
            if fault == 'error': raise RuntimeError('unknown close outcome')
    fake = Fake()
    result = close(store, None, herdr=fake)
    assert bool(result.get('closed')) == (fault == 'none')
    assert fake.calls == (1 if fault in ('none', 'error') else 0)
    again = close(store, None, herdr=fake)
    assert bool(again.get('closed')) == (fault == 'none')
    assert fake.calls == (1 if fault in ('none', 'error') else 0)


def test_orchestrator_launch_has_durable_cleanup_identity(setup, monkeypatch):
    from test_config import _write_yaml
    import yaml
    root, _ = setup
    roles = yaml.safe_load((root/'roles.yaml').read_text())
    roles['orchestrator'] = {**roles['qa'], 'readonly': True}
    _write_yaml(root/'roles.yaml', roles)
    runtime, _ = make_run(setup)
    monkeypatch.setenv('CREW_ROLE', 'lead')
    adapter = FakeHerdr()
    state = runtime.start(herdr=adapter)
    orch = state['orchestrator']
    assert orch['launch_intent'] and orch['mode'] == 'interactive'
    assert state['owned_panes'] == [orch['pane_id']]
    monkeypatch.setenv('CREW_ROLE', 'orchestrator')
    runtime.request_control('close')
    result = runtime.tick(herdr=adapter, resource_check=ready)
    assert adapter.closed == [orch['pane_id']]
    assert result['cleanup']['orchestrator']['closed']
    assert result['cleanup']['retained_resources']


def test_stop_then_completed_oneshot_receipt_closes(setup, monkeypatch):
    from test_config import _write_yaml
    from crew.receipt import write_receipt
    import yaml
    root, _ = setup
    roles = yaml.safe_load((root/'roles.yaml').read_text())
    roles['qa'].update(mode='oneshot', args=['{prompt}'])
    _write_yaml(root/'roles.yaml', roles)
    runtime, _ = make_run(setup)
    monkeypatch.setenv('CREW_ROLE', 'orchestrator')
    class Adapter(FakeHerdr):
        def get_pane(self, pane): return {'pane_id': pane, 'tab_id': 'w1:t1', 'terminal_id': 'term-'+pane}
        def process_info(self, pane): return {'pane_id': pane, 'shell_pid': 123,
            'foreground_process_group_id': 123, 'foreground_processes': [{'pid': 123}]}
        def run_pane(self, pane, command): pass
    adapter = Adapter(); runtime.start(herdr=adapter)
    runtime.tick(herdr=adapter, resource_check=ready)
    runtime.request_control('stop')
    result = runtime.tick(herdr=adapter, resource_check=ready)
    assert result['cleanup']['active_tasks'] and not adapter.closed
    for task in runtime.store.read_state()['tasks'].values():
        (runtime.path/task['artifact']).write_text('body\nQA DONE\n')
        write_receipt(runtime.path/Path(task['artifact']).parent/('.exit-'+task['launch_intent']+'.json'), task['launch_intent'], 0)
    runtime.request_control('close')
    result = runtime.tick(herdr=adapter, resource_check=ready)
    assert len(adapter.closed) == 2 and not result['cleanup']['active_tasks']
    assert all(t['status'] == 'done' and t['closed'] for t in result['tasks'].values())


def test_resource_queued_worktree_cleanup(mutable):
    runtime, adapter = run(mutable)
    low = lambda *a, **k: type('Low', (), {'ready': False})()
    runtime.tick(herdr=adapter, resource_check=low)
    records = [r for lane in runtime.store.read_state()['lanes'] for r in lane['worktrees']]
    assert not adapter.cwds and all(Path(r['path']).exists() for r in records)
    runtime.request_control('close')
    result = runtime.tick(herdr=adapter, resource_check=low)
    assert all(not Path(r['path']).exists() for r in records)
    assert all(r['status'] == 'removed' for r in result['cleanup']['worktrees'])


@pytest.mark.parametrize('fault', ['symlink', 'branch', 'registration', 'changed_state', 'changed_writer', 'lost_result'])
def test_native_cleanup_exact_ownership_and_retry(mutable, monkeypatch, fault):
    import crew.worktrees as worktrees
    from test_worktrees import git
    runtime, adapter = run(mutable)
    low = lambda *a, **k: type('Low', (), {'ready': False})()
    runtime.tick(herdr=adapter, resource_check=low)
    state = runtime.store.read_state(); record = state['lanes'][0]['worktrees'][0]; path = Path(record['path'])
    if fault == 'symlink':
        moved = path.with_name('moved'); path.rename(moved); path.symlink_to(moved, target_is_directory=True)
    elif fault == 'branch': git(path, 'switch', '-c', 'foreign')
    elif fault == 'registration':
        record['git_dir_identity'][1] += 1; runtime.store.write_state(state)
    original = worktrees.git; injected = [False]
    def changed(repository, *args):
        result = original(repository, *args)
        if not injected[0] and args[:1] == ('status',) and str(repository) == str(path):
            injected[0] = True
            if fault in ('changed_state', 'changed_writer'):
                def mutate(s):
                    if fault == 'changed_state': s['lanes'][0]['worktrees'][0]['path_identity'] = [0, 0]
                    else:
                        task = next(iter(s['tasks'].values()))
                        task.update(status='running', launch_intent='late', pane_id='unknown')
                    return s
                runtime.store.update_state(mutate)
        if fault == 'lost_result' and args[:2] == ('worktree', 'remove') and str(path) in args:
            raise OSError('lost remove result')
        return result
    monkeypatch.setattr(worktrees, 'git', changed)
    runtime.request_control('close')
    result = runtime.tick(herdr=adapter, resource_check=low)
    if fault == 'lost_result':
        assert not path.exists()
        assert result['cleanup']['worktrees'][0]['status'] == 'removed'
        assert runtime.tick(herdr=adapter, resource_check=low)['cleanup']['worktrees'][0]['status'] == 'removed'
    else:
        assert path.exists() and result['cleanup']['worktrees'][0]['status'] == 'retained'


def test_cli_reports_retained_cleanup(setup, monkeypatch):
    from test_runtime_cli import cli
    runtime, _ = make_run(setup)
    monkeypatch.setenv('CREW_ROLE', 'orchestrator')
    adapter = FakeHerdr(); runtime.start(herdr=adapter)
    runtime.tick(herdr=adapter, resource_check=ready)
    runtime.request_control('stop'); runtime.tick(herdr=adapter, resource_check=ready)
    result = cli(setup, 'status', 'run1', role='lead')
    assert result.returncode == 0 and 'active_tasks' in result.stdout and 'stopped: false' in result.stdout
    denied = cli(setup, 'tick', 'run1', role='worker')
    assert denied.returncode == 1 and 'CREW_ROLE=orchestrator required' in denied.stdout


def test_orchestrator_uncertain_launch_keeps_known_resource(setup, monkeypatch):
    from test_config import _write_yaml
    from crew.launch import LaunchResult
    import yaml
    root, _ = setup
    roles = yaml.safe_load((root/'roles.yaml').read_text())
    roles['orchestrator'] = {**roles['qa'], 'readonly': True}
    _write_yaml(root/'roles.yaml', roles)
    runtime, _ = make_run(setup)
    monkeypatch.setenv('CREW_ROLE', 'lead')
    adapter = FakeHerdr()
    def interrupted(*args, **kwargs):
        pane = 'w1:p1'; adapter.owned_panes.add(pane)
        error = RuntimeError('uncertain launch')
        error.launch_result = LaunchResult('w1:t1', pane, 'uncertain')
        raise error
    monkeypatch.setattr('crew.runtime.launch', interrupted)
    with pytest.raises(ValueError, match='start uncertain'): runtime.start(herdr=adapter)
    state = runtime.store.read_state()
    assert state['orchestrator']['pane_id'] == 'w1:p1' and state['owned_panes'] == ['w1:p1']
    monkeypatch.setenv('CREW_ROLE', 'orchestrator'); runtime.request_control('close')
    result = runtime.tick(herdr=adapter, resource_check=ready)
    assert not adapter.closed and not result['cleanup']['closed']
    assert any(r['kind'] == 'start' for r in result['cleanup']['retained_resources'])


def test_failed_oneshot_receipt_cleans_without_output(tmp_path, monkeypatch):
    monkeypatch.setenv('CREW_ROLE', 'orchestrator')
    store = store_at(tmp_path, 'failed')
    store.update_state(lambda s: (s['tasks']['A'].update(mode='oneshot', exit_code=7,
        process_identity={'terminal_id':'term1', 'shell_pid':123}) or s))
    class Fake:
        def get_pane(self, pane): return {'pane_id':pane, 'tab_id':'w:t1', 'terminal_id':'term1'}
        def process_info(self, pane): return {'pane_id':pane, 'shell_pid':123,
            'foreground_process_group_id':123, 'foreground_processes':[{'pid':123}]}
        def command(self, *args): pass
    assert close(store, 'A', herdr=Fake(), read_receipt=lambda: {'launch_intent':'i1','exit_code':7})['closed']
