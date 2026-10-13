"""Isolated runtime and subprocess CLI behavior."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import pytest
import yaml
from test_config import _global_config, _write_yaml
from crew.resources import ResourceReport, QuotaAvailability

def api():
    assert importlib.util.find_spec('crew.runtime') is not None, 'runtime module missing'
    from crew.runtime import Runtime, create_run, init_project, config_set
    return Runtime, create_run, init_project, config_set

@pytest.fixture
def setup(tmp_path):
    root = _global_config(tmp_path / 'global')
    project = tmp_path / 'project'
    project.mkdir()
    return root, project

def ready(*a, **k):
    return ResourceReport(8, .1, k['active_workers'], QuotaAvailability('available', 1000), True)

class FakeHerdr:
    def __init__(self):
        self.owned_panes=set(); self.agents={}; self.prompts=[]; self.closed=[]
    def create_tab(self, workspace, cwd, label, **kw):
        pane=f'w1:p{len(self.agents)+1}'
        self.owned_panes.add(pane)
        self.agents[pane]={'pane_id':pane,'agent_status':'idle','agent_session':{
            'kind':'id','source':'integration:fake','agent':'fake','value':pane}}
        return 'w1:t1',pane
    def start(self,name,kind,pane,args,**kw): return self.agents[pane]
    def prompt(self,pane,message,**kw):
        from crew.herdr import PromptOutcome
        self.prompts.append(message)
        return PromptOutcome('settled',self.agents[pane])
    def get_agent(self,pane): return self.agents[pane]
    def command(self,*args):
        if args[:2]==('workspace','create'): return {'workspace':{'workspace_id':'w1'}}
        if args[:2]==('pane','close'): self.closed.append(args[2]); return {}
        raise AssertionError(args)

def make_run(setup,**kw):
    Runtime,create_run,_,_=api()
    path=create_run(*setup,'run1','readonly',['A','B'],**kw)
    return Runtime(path),path

def test_freeze_lanes(setup):
    runtime,path=make_run(setup,groups=2)
    state=runtime.store.read_state()
    assert state['items']==['A','B'] and len(state['lanes'])==2 and state['tasks']=={}
    _write_yaml(setup[0]/'config.yaml',{'broken':True})
    assert runtime.config.roles['qa'].model=='haiku'
    assert yaml.safe_load((path/'run.yaml').read_text())['roles']['qa']['model']=='haiku'

@pytest.mark.parametrize('value',['../escape','','a/b','a;touch x'])
def test_identifier_rejection(setup,value):
    _,create_run,_,_=api()
    with pytest.raises(ValueError): create_run(*setup,value,'readonly',['A'])
    assert not (setup[1]/'.crew/runs').exists()

def test_duplicate_and_groups_rejection(setup):
    _,create_run,_,_=api()
    with pytest.raises(ValueError): create_run(*setup,'one','readonly',['A','A'])
    with pytest.raises(ValueError): create_run(*setup,'one','readonly',['A'],groups=0)

def test_project_init_no_overwrite(setup):
    _,_,init_project,_=api()
    root,project=setup
    (root/'prompts').mkdir(); (root/'prompts/qa.md').write_text('prompt')
    (project/'.crew').mkdir(); (project/'.crew/project.yaml').write_text('rules: [preserve]\n')
    init_project(root,project); init_project(root,project)
    assert (project/'.crew/project.yaml').read_text()=='rules: [preserve]\n'
    assert (project/'.crew/prompts/qa.md').read_text()=='prompt'

def test_config_set_validated(setup):
    _,_,_,config_set=api()
    config_set(*setup,'resources.max_workers=3',scope='project')
    path=setup[1]/'.crew/project.yaml'
    before=path.read_bytes()
    assert yaml.safe_load(before)['resources']['max_workers']==3
    with pytest.raises(ValueError): config_set(*setup,'resources.max_workers=0',scope='project')
    assert path.read_bytes()==before

def test_permissions(setup,monkeypatch):
    _,create_run,_,_=api()
    monkeypatch.setenv('CREW_ROLE','worker')
    with pytest.raises(PermissionError): create_run(*setup,'one','readonly',['A'])
    monkeypatch.setenv('CREW_ROLE','lead')
    runtime,_=make_run(setup); before=runtime.store.read_state()
    monkeypatch.setenv('CREW_ROLE','worker')
    for call in (lambda:runtime.request_control('pause'),lambda:runtime.decide('x','approved'),
                 lambda:runtime.tick(herdr=FakeHerdr(),resource_check=ready)):
        with pytest.raises(PermissionError): call()
    assert runtime.store.read_state()==before

def test_lifecycle(setup,monkeypatch):
    runtime,path=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    adapter=FakeHerdr(); runtime.start(herdr=adapter)
    runtime.tick(herdr=adapter,resource_check=ready)
    tasks=runtime.store.read_state()['tasks']
    assert len(tasks)==2 and all(t['status']=='running' for t in tasks.values())
    for task in tasks.values(): (path/task['artifact']).write_text('Report complete\nQA DONE\n')
    result=runtime.tick(herdr=adapter,resource_check=ready)
    assert result['flows']=={'A':'complete','B':'complete'}
    assert all(t['status']=='done' for t in runtime.store.read_state()['tasks'].values())
    runtime.tick(herdr=adapter,resource_check=ready)
    assert len(adapter.prompts)==2 and 'QA DONE' in adapter.prompts[0]
    runtime.request_control('close',keep_worktrees=True)
    runtime.tick(herdr=adapter,resource_check=ready)
    assert len(adapter.closed)==2

def test_low_memory(setup,monkeypatch):
    runtime,_=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    adapter=FakeHerdr(); runtime.start(herdr=adapter)
    low=lambda *a,**k:ResourceReport(0,.1,k['active_workers'],QuotaAvailability('available',100),False)
    runtime.tick(herdr=adapter,resource_check=low)
    assert all(t['status']=='queued' for t in runtime.store.read_state()['tasks'].values())
    assert adapter.prompts==[]

def test_dispatch_records_resource_and_quota_wait_reason(setup,monkeypatch):
    runtime,_=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    adapter=FakeHerdr(); runtime.start(herdr=adapter)
    unknown=lambda *a,**k:ResourceReport(8,.1,k['active_workers'],QuotaAvailability('unknown',None),False)
    runtime.tick(herdr=adapter,resource_check=unknown)
    tasks=runtime.store.read_state()['tasks']
    assert tasks and all(t['status']=='queued' and t['resource_wait']=='model quota unknown' for t in tasks.values())
    unavailable=lambda *a,**k:ResourceReport(8,.1,k['active_workers'],QuotaAvailability('unavailable',0),False)
    runtime.tick(herdr=adapter,resource_check=unavailable)
    tasks=runtime.store.read_state()['tasks']
    assert all('quota below' in t['resource_wait'] for t in tasks.values())
    assert adapter.prompts==[]


@pytest.mark.parametrize('bad',['symlink','escape','stale','directory','shared'])
def test_artifact_rejected(setup,monkeypatch,tmp_path,bad):
    runtime,path=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    adapter=FakeHerdr(); runtime.start(herdr=adapter); runtime.tick(herdr=adapter,resource_check=ready)
    tasks=runtime.store.read_state()['tasks']; task_id=next(iter(tasks)); task=tasks[task_id]
    output=path/task['artifact']
    if bad=='symlink':
        other=tmp_path/'outside'; other.write_text('body\nQA DONE\n'); output.symlink_to(other)
    elif bad=='escape': runtime.store.update_state(lambda s:(s['tasks'][task_id].update(artifact='../outside') or s))
    elif bad=='stale': output.write_text('body\nQA DONE\n'); os.utime(output,ns=(1,1))
    elif bad=='directory': output.mkdir()
    else: runtime.store.update_state(lambda s:(s['tasks'][task_id].update(artifact=list(tasks.values())[1]['artifact']) or s))
    result=runtime.tick(herdr=adapter,resource_check=ready)
    assert runtime.store.read_state()['tasks'][task_id]['status']!='done'
    assert result['flows']['A']!='complete'

def test_native_mismatch(setup,monkeypatch):
    runtime,path=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    adapter=FakeHerdr(); runtime.start(herdr=adapter); runtime.tick(herdr=adapter,resource_check=ready)
    for task in runtime.store.read_state()['tasks'].values():
        (path/task['artifact']).write_text('body\nQA DONE\n')
        adapter.agents[task['pane_id']]={**adapter.agents[task['pane_id']],'agent_session':{}}
    runtime.tick(herdr=adapter,resource_check=ready)
    assert all(t['status']=='blocked' for t in runtime.store.read_state()['tasks'].values())
    assert len(adapter.prompts)==2

def test_decision_round(setup,monkeypatch):
    _write_yaml(setup[0]/'flows/readonly.yaml',{'steps':[{'team':'validator','decision':'user'}],'max_fail_rounds':1})
    runtime,_=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    adapter=FakeHerdr(); runtime.start(herdr=adapter); runtime.tick(herdr=adapter,resource_check=ready)
    key='A:readonly:s0:r0:decision'
    assert runtime.store.read_state()['decisions'][key]['status']=='pending'
    monkeypatch.setenv('CREW_ROLE','lead')
    with pytest.raises(ValueError): runtime.decide('A:readonly:s0:r1:decision','approved')
    runtime.decide(key,'approved'); runtime.decide(key,'approved')
    with pytest.raises(ValueError): runtime.decide(key,'rejected')
    monkeypatch.setenv('CREW_ROLE','orchestrator')
    runtime.tick(herdr=adapter,resource_check=ready)
    assert len(adapter.prompts)==1

def test_concurrent_tick(setup,monkeypatch):
    runtime,_=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    adapter=FakeHerdr(); runtime.start(herdr=adapter)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:runtime.tick(herdr=adapter,resource_check=ready),range(2)))
    assert len(adapter.prompts)==2

def cli(setup,*args,role='lead'):
    return subprocess.run([sys.executable,'-m','crew.cli','--global-dir',str(setup[0]),'--project',str(setup[1]),*args],
        capture_output=True,text=True,env={**os.environ,'CREW_ROLE':role,'PYTHONDONTWRITEBYTECODE':'1'})

def test_cli(setup):
    assert importlib.util.find_spec('crew.cli') is not None,'CLI module missing'
    home=cli(setup); assert home.returncode==0 and 'runs' in home.stdout and 'help' in home.stdout
    help_result=cli(setup,'run','new','--help'); assert help_result.returncode==0 and '--flow' in help_result.stdout
    bad=cli(setup,'run','new','--unknown'); assert bad.returncode==2 and 'error:' in bad.stdout and not bad.stderr
    new=cli(setup,'run','new','--id','one','--flow','readonly','--items','A')
    assert new.returncode==0,new.stdout
    status=cli(setup,'status','one','--fields','run_id,tasks')
    assert status.returncode==0 and 'run_id: one' in status.stdout
    denied=cli(setup,'tick','one',role='worker')
    assert denied.returncode==1 and 'orchestrator' in denied.stdout

def test_cli_status_reports_actionable_launch_errors(setup,monkeypatch):
    monkeypatch.setenv('CREW_ROLE','lead')
    runtime,_=make_run(setup)
    runtime.store.update_state(lambda s:(s['tasks'].update({'A:readonly:s0:r0:role0':{
        'status':'blocked','team':'validator','role':'qa','launch_error':'launch boom',
        'reconciliation_error':'receipt mismatch','resource_wait':'model quota unknown'}}) or s))
    result=cli(setup,'status','run1')
    assert result.returncode==0,result.stdout
    assert 'launch boom' in result.stdout and 'receipt mismatch' in result.stdout
    assert 'resource_wait' in result.stdout and 'model quota unknown' in result.stdout
    runtime.store.update_state(lambda s:(s.update(start_uncertain=True,start_error='start boom') or s))
    result=cli(setup,'status','run1')
    assert result.returncode==0 and 'start boom' in result.stdout


def test_cli_advance_rejects_extreme_delay(setup):
    low=cli(setup,'advance','run1','--min-delay','0.01')
    assert low.returncode==1 and 'min_delay' in low.stdout
    high=cli(setup,'advance','run1','--min-delay','1','--max-delay','600')
    assert high.returncode==1 and 'max_delay' in high.stdout


def test_axi_pending(setup):
    assert importlib.util.find_spec('crew.cli') is not None,'CLI module missing'
    result=cli(setup,'axi','install')
    assert result.returncode==0 and 'pending' in result.stdout and 'approval' in result.stdout


def test_evidence_mutation_during_bridge_lock_blocks(setup,monkeypatch):
    runtime,path=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    adapter=FakeHerdr(); runtime.start(herdr=adapter); runtime.tick(herdr=adapter,resource_check=ready)
    tasks=runtime.store.read_state()['tasks']
    for task in tasks.values(): (path/task['artifact']).write_text('body\nQA DONE\n')
    runtime.tick(herdr=adapter,resource_check=ready)
    import crew.runtime as module
    original=module.persist_next_tasks
    def mutate(store,*args,**kwargs):
        task=next(iter(runtime.store.read_state()['tasks'].values()))
        (path/task['artifact']).write_text('changed\nQA DONE\n')
        return original(store,*args,**kwargs)
    monkeypatch.setattr(module,'persist_next_tasks',mutate)
    result=runtime.tick(herdr=adapter,resource_check=ready)
    assert result['flows']['A']=='blocked'


def test_ask_decide_explicit_option(setup,monkeypatch):
    runtime,_=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    runtime.ask('Choose?', ['a','b'])
    state=runtime.store.read_state(); key=next(iter(state['decisions']))
    monkeypatch.setenv('CREW_ROLE','lead')
    with pytest.raises(ValueError): runtime.decide(key,'c')
    runtime.decide(key,'a')
    assert runtime.store.read_state()['decisions'][key]['choice']=='a'


def test_handoff_placeholders_resolved_and_input_supplied(setup,monkeypatch):
    _write_yaml(setup[0]/'teams.yaml',{'validator':{'roles':['qa'],'handoffs':{'qa':{
        'in':'tickets/{ticket}/bug.md','out':'tickets/{ticket}/result-r{round}.md'}},'done_marker':'QA DONE','axis':[]}})
    runtime,path=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    adapter=FakeHerdr(); runtime.start(herdr=adapter); runtime.tick(herdr=adapter,resource_check=ready)
    task=next(iter(runtime.store.read_state()['tasks'].values()))
    assert '{ticket}' not in task['artifact'] and '{round}' not in task['artifact']
    assert 'Input:' in adapter.prompts[0] and 'tickets/A/bug.md' in adapter.prompts[0]


def test_cli_optional_generated_run_id(setup):
    result=cli(setup,'run','new','--flow','readonly','--items','A')
    assert result.returncode==0,result.stdout
    assert len(list((setup[1]/'.crew/runs').iterdir()))==1


def test_frozen_config_change_blocks(setup,monkeypatch):
    runtime,path=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    value=yaml.safe_load((path/'run.yaml').read_text()); value['roles']['qa']['model']='changed'
    (path/'run.yaml').write_text(yaml.safe_dump(value))
    with pytest.raises(ValueError,match='frozen'): runtime.tick(herdr=FakeHerdr(),resource_check=ready)


def test_cli_fields_usage_does_not_mutate(setup):
    result=cli(setup,'run','new','--id','one','--flow','readonly','--items','A','--fields','unknown')
    assert result.returncode==2
    assert not (setup[1]/'.crew/runs/one').exists()


def test_start_injects_orchestrator_role(setup,monkeypatch):
    roles=yaml.safe_load((setup[0]/'roles.yaml').read_text()); roles['orchestrator']=roles['qa']
    _write_yaml(setup[0]/'roles.yaml',roles)
    runtime,_=make_run(setup); monkeypatch.setenv('CREW_ROLE','lead')
    class Adapter(FakeHerdr):
        def create_tab(self,*a,**kw):
            self.env=kw.get('env',{})
            return super().create_tab(*a,**kw)
    adapter=Adapter(); runtime.start(herdr=adapter)
    assert adapter.env.get('CREW_ROLE')=='orchestrator'
    assert '--project' in adapter.prompts[0]


def test_oneshot_receipt_lifecycle(setup,monkeypatch):
    # External boundary runs a harmless local Python command, never Herdr.
    roles=yaml.safe_load((setup[0]/'roles.yaml').read_text())
    roles['qa'].update(cli=sys.executable,mode='oneshot',args=['-c',
        'import pathlib,re,sys; p=re.search(r"Output: (.+)",sys.argv[1])[1]; pathlib.Path(p).write_text("body\\nQA DONE\\n")', '{prompt}'])
    _write_yaml(setup[0]/'roles.yaml',roles)
    runtime,_=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    class Adapter(FakeHerdr):
        def run_pane(self,pane,command):
            result=subprocess.run(command,shell=True,capture_output=True,text=True)
            assert result.returncode==0,result.stderr
    adapter=Adapter(); runtime.start(herdr=adapter); runtime.tick(herdr=adapter,resource_check=ready)
    result=runtime.tick(herdr=adapter,resource_check=ready)
    assert result['flows']=={'A':'complete','B':'complete'}


def test_dispatch_forged_future_task_denied(setup,monkeypatch):
    runtime,_=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    adapter=FakeHerdr(); runtime.start(herdr=adapter)
    key='A:readonly:s0:r7:role0'
    runtime.store.update_state(lambda s:(s['tasks'].update({key:{'status':'queued','role':'qa','team':'validator','step':0,'round':7}}) or s))
    with pytest.raises(ValueError): runtime.dispatch(key,herdr=adapter,resource_check=ready)
    assert not adapter.prompts


def test_receipt_writer_rejects_symlink(tmp_path):
    assert importlib.util.find_spec('crew.receipt') is not None,'receipt module missing'
    from crew.receipt import write_receipt
    output=tmp_path/'exit.json'; outside=tmp_path/'outside'; outside.write_text('preserve')
    output.symlink_to(outside)
    with pytest.raises(ValueError): write_receipt(output,'abc',0)
    assert outside.read_text()=='preserve'


def test_worker_launch_drops_orchestrator_permission(setup,monkeypatch):
    runtime,_=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    class Adapter(FakeHerdr):
        def create_tab(self,*a,**kw):
            self.last_env=kw.get('env',{})
            return super().create_tab(*a,**kw)
    adapter=Adapter(); runtime.start(herdr=adapter); runtime.tick(herdr=adapter,resource_check=ready)
    assert adapter.last_env.get('CREW_ROLE')=='worker'


@pytest.mark.parametrize('kind', ['question', 'flow'])
def test_orchestrator_subprocess_cannot_decide(setup, monkeypatch, kind):
    if kind == 'flow':
        _write_yaml(setup[0]/'flows/readonly.yaml', {'steps': [{'team':'validator','decision':'user'}], 'max_fail_rounds':1})
    runtime,_=make_run(setup)
    monkeypatch.setenv('CREW_ROLE','orchestrator')
    if kind == 'question':
        runtime.ask('Proceed?', ['yes','no'])
        key=next(iter(runtime.store.read_state()['decisions']))
        choice='yes'
    else:
        adapter=FakeHerdr(); runtime.start(herdr=adapter); runtime.tick(herdr=adapter,resource_check=ready)
        key='A:readonly:s0:r0:decision'; choice='approved'
    before=runtime.store.read_state()
    denied=cli(setup,'decide','run1',key,choice,role='orchestrator')
    assert denied.returncode==1 and 'CREW_ROLE=lead required' in denied.stdout
    assert runtime.store.read_state()==before
    assert runtime.store.read_state()['decisions'][key]['status']=='pending'
    accepted=cli(setup,'decide','run1',key,choice,role='lead')
    assert accepted.returncode==0,accepted.stdout
    assert runtime.store.read_state()['decisions'][key]['status']=='approved'


def test_decide_requires_explicit_lead_even_without_role(setup,monkeypatch):
    runtime,_=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    runtime.ask('Proceed?', ['yes','no']); key=next(iter(runtime.store.read_state()['decisions']))
    before=runtime.store.read_state(); monkeypatch.delenv('CREW_ROLE')
    with pytest.raises(PermissionError,match='CREW_ROLE=lead required'): runtime.decide(key,'yes')
    assert runtime.store.read_state()==before


def test_runtime_run_yaml_duplicate_decode_rejected(setup):
    runtime,path=make_run(setup)
    config_path=path/'run.yaml'
    config_path.write_text(config_path.read_text()+'herdr_session: crew-lab\n')
    with pytest.raises(ValueError,match='duplicate YAML key'): type(runtime)(path)
    with pytest.raises(ValueError,match='duplicate YAML key'): runtime.check_config()


def test_runtime_config_set_duplicate_assignment_rejected(setup):
    _,_,_,config_set=api()
    with pytest.raises(ValueError,match='duplicate YAML key'):
        config_set(*setup,'resources={max_workers: 1, max_workers: 3}',scope='project')
    assert not (setup[1]/'.crew/project.yaml').exists()


def test_oneshot_reconciles_matching_receipt_despite_launch_error(setup,monkeypatch):
    roles=yaml.safe_load((setup[0]/'roles.yaml').read_text())
    roles['qa'].update(cli=sys.executable,mode='oneshot',args=['-c',
        'import pathlib,re,sys; p=re.search(r"Output: (.+)",sys.argv[1])[1]; pathlib.Path(p).write_text("body\\nQA DONE\\n")', '{prompt}'])
    _write_yaml(setup[0]/'roles.yaml',roles)
    monkeypatch.setenv('CREW_ROLE','lead')
    runtime,_=make_run(setup); monkeypatch.setenv('CREW_ROLE','orchestrator')
    class Adapter(FakeHerdr):
        def run_pane(self,pane,command):
            result=subprocess.run(command,shell=True,capture_output=True,text=True)
            assert result.returncode==0,result.stderr
            raise RuntimeError('submission uncertain')
    adapter=Adapter(); runtime.start(herdr=adapter)
    runtime.tick(herdr=adapter,resource_check=ready)
    blocked=runtime.store.read_state()['tasks']
    assert all(t['status']=='blocked' and t.get('launch_error') for t in blocked.values())
    result=runtime.tick(herdr=adapter,resource_check=ready)
    assert result['flows']=={'A':'complete','B':'complete'}
    assert all(t['status']=='done' for t in runtime.store.read_state()['tasks'].values())


def test_start_uncertain_stores_error_and_blocks_retry(setup,monkeypatch):
    monkeypatch.setenv('CREW_ROLE','lead')
    runtime,_=make_run(setup)
    class Failing(FakeHerdr):
        def create_tab(self,*a,**kw): raise RuntimeError('runner tab unavailable')
    with pytest.raises(ValueError,match='start uncertain'): runtime.start(herdr=Failing(),controller='script')
    state=runtime.store.read_state()
    assert state.get('start_uncertain') is True
    assert 'runner tab unavailable' in state.get('start_error','')
    calls=[]
    class Recording(FakeHerdr):
        def command(self,*a,**kw): calls.append(a); return {'workspace':{'workspace_id':'w1'}}
        def create_tab(self,*a,**kw): calls.append(a); return ('w1:t1','w1:p1')
    with pytest.raises(ValueError,match='start uncertain'): runtime.start(herdr=Recording(),controller='script')
    assert calls==[]


def _oneshot_uncertain(setup,monkeypatch,command,*,execute):
    roles=yaml.safe_load((setup[0]/'roles.yaml').read_text())
    roles['qa'].update(cli=sys.executable,mode='oneshot',args=['-c',command,'{prompt}'])
    _write_yaml(setup[0]/'roles.yaml',roles)
    monkeypatch.setenv('CREW_ROLE','lead')
    runtime,path=make_run(setup)
    monkeypatch.setenv('CREW_ROLE','orchestrator')
    class Adapter(FakeHerdr):
        def run_pane(self,pane,cmd):
            if execute:
                result=subprocess.run(cmd,shell=True,capture_output=True,text=True)
                assert result.returncode==0,result.stderr
            raise RuntimeError('submission uncertain')
    adapter=Adapter(); runtime.start(herdr=adapter)
    runtime.tick(herdr=adapter,resource_check=ready)
    return runtime,path,adapter


def _only_task(runtime):
    return next(iter(runtime.store.read_state()['tasks'].values()))


def _receipt(path,task):
    return path/Path(task['artifact']).parent/('.exit-'+task['launch_intent']+'.json')


def test_oneshot_uncertain_nonzero_receipt_marks_failed(setup,monkeypatch):
    command='import pathlib,re,sys; p=re.search(r"Output: (.+)",sys.argv[1])[1]; pathlib.Path(p).write_text("body\\nQA DONE\\n"); sys.exit(1)'
    runtime,path,adapter=_oneshot_uncertain(setup,monkeypatch,command,execute=True)
    assert _only_task(runtime)['status']=='blocked' and _only_task(runtime).get('launch_error')
    runtime.tick(herdr=adapter,resource_check=ready)
    tasks=runtime.store.read_state()['tasks']
    assert all(t['status']=='failed' and t['exit_code']==1 for t in tasks.values())


def test_oneshot_uncertain_missing_receipt_stays_blocked(setup,monkeypatch):
    runtime,path,adapter=_oneshot_uncertain(setup,monkeypatch,'pass',execute=False)
    runtime.tick(herdr=adapter,resource_check=ready)
    tasks=runtime.store.read_state()['tasks']
    assert all(t['status']=='blocked' for t in tasks.values())


def test_oneshot_uncertain_invalid_receipt_stays_blocked(setup,monkeypatch):
    runtime,path,adapter=_oneshot_uncertain(setup,monkeypatch,'pass',execute=False)
    for task in runtime.store.read_state()['tasks'].values():
        _receipt(path,task).write_text('{"nope": true}\n')
    runtime.tick(herdr=adapter,resource_check=ready)
    tasks=runtime.store.read_state()['tasks']
    assert all(t['status']!='done' and t.get('reconciliation_error') for t in tasks.values())


def test_oneshot_uncertain_wrong_intent_receipt_stays_blocked(setup,monkeypatch):
    runtime,path,adapter=_oneshot_uncertain(setup,monkeypatch,'pass',execute=False)
    for task in runtime.store.read_state()['tasks'].values():
        _receipt(path,task).write_text('{"launch_intent":"deadbeefdeadbeefdeadbeefdeadbeef","exit_code":0}')
    runtime.tick(herdr=adapter,resource_check=ready)
    tasks=runtime.store.read_state()['tasks']
    assert all(t['status']!='done' and t.get('reconciliation_error') for t in tasks.values())


def test_oneshot_uncertain_ownership_mismatch_stays_blocked(setup,monkeypatch):
    runtime,path,adapter=_oneshot_uncertain(setup,monkeypatch,'pass',execute=False)
    for task in runtime.store.read_state()['tasks'].values():
        _receipt(path,task).write_text('{"launch_intent":"'+task['launch_intent']+'","exit_code":0}')
    runtime.store.update_state(lambda s:(s.update(owned_panes=[]) or s))
    runtime.tick(herdr=adapter,resource_check=ready)
    tasks=runtime.store.read_state()['tasks']
    assert all(t['status']!='done' and t.get('reconciliation_error') for t in tasks.values())


def test_oneshot_uncertain_stale_artifact_does_not_complete(setup,monkeypatch):
    command='import pathlib,re,sys; p=re.search(r"Output: (.+)",sys.argv[1])[1]; pathlib.Path(p).write_text("body\\nQA DONE\\n")'
    runtime,path,adapter=_oneshot_uncertain(setup,monkeypatch,command,execute=True)
    for task in runtime.store.read_state()['tasks'].values():
        os.utime(path/task['artifact'],ns=(1,1))
    runtime.tick(herdr=adapter,resource_check=ready)
    assert all(t['status']!='done' for t in runtime.store.read_state()['tasks'].values())


def test_runtime_config_set_duplicate_existing_file_rejected(setup):
    _,_,_,config_set=api()
    path=setup[1]/'.crew/project.yaml'; path.parent.mkdir()
    path.write_text('resources:\n  max_workers: 1\nresources:\n  max_workers: 4\n')
    before=path.read_bytes()
    with pytest.raises(ValueError,match='duplicate YAML key'):
        config_set(*setup,'resources.max_workers=2',scope='project')
    assert path.read_bytes()==before
