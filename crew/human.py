"""Terminal presentation of CLI results; does not change runtime data."""
from collections import Counter
from pathlib import Path
import shlex


# Only schema labels are translated. Dynamic map keys remain exact identifiers.
LABELS = {
    'run_id': 'Run', 'path': 'Path', 'project': 'Project path', 'bin': 'Executable',
    'help': 'Next', 'ready': 'Ready', 'control_requests': 'Control requests',
    'status': 'Status', 'tasks': 'Tasks', 'decisions': 'Decisions', 'flows': 'Flows',
    'flow': 'Flow', 'workspace': 'Workspace', 'cleanup': 'Cleanup', 'tools': 'Tools',
    'integration': 'Integration', 'error': 'Error', 'reason': 'Reason', 'fields': 'Fields',
    'models': 'Models', 'model': 'Model', 'quota': 'Quota', 'active_workers': 'Active workers',
    'free_memory_gb': 'Free memory GB', 'load_per_core': 'Load per core',
    'runway_seconds': 'Runway seconds', 'reconciliation_needed': 'Reconciliation needed',
    'launch_error': 'Launch error', 'close_error': 'Close error', 'available': 'Available',
    'name': 'Name', 'install': 'Install', 'config': 'Configuration', 'scope': 'Scope',
    'restore_agents': 'Restore agents', 'live_smoke': 'Live smoke', 'role': 'Role',
    'team': 'Team', 'id': 'ID', 'options': 'Options', 'question': 'Question', 'choice': 'Choice',
    'kind': 'Kind', 'panes': 'Panes', 'pane_id': 'Pane', 'action': 'Action',
    'keep_worktrees': 'Keep worktrees', 'closed': 'Closed', 'stopped': 'Stopped',
    'active_tasks': 'Active tasks', 'orchestrator': 'Orchestrator', 'worktrees': 'Worktrees',
    'retained_branches': 'Retained branches', 'retained_resources': 'Retained resources',
    'repository': 'Repository', 'branch': 'Branch', 'description': 'Description',
    'count': 'Count', 'runs': 'Runs', 'artifact': 'Artifact', 'launch_cwd': 'Working directory',
    'reconciliation_error': 'Reconciliation error', 'close_identity_changed': 'Close identity changed',
    'pause_requested': 'Pause requested', 'stop_requested': 'Stop requested',
    'close_requested': 'Close requested', 'start_uncertain': 'Start uncertain',
    'exit_code': 'Exit code', 'launch': 'Launch', 'close': 'Close',
}
DYNAMIC_MAPS = {'tasks', 'decisions', 'flows', 'control_requests', 'panes'}


def label(key):
    return LABELS.get(key, key)


def lines(value, indent=0, *, dynamic_keys=False):
    """Keep every nested field, including unknown and retained-resource reasons."""
    prefix = ' ' * indent
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key) if dynamic_keys else label(str(key))
            if isinstance(item, (dict, list)) and item:
                yield f'{prefix}{name}:'
                yield from lines(item, indent + 2, dynamic_keys=key in DYNAMIC_MAPS)
            else:
                text = 'none' if isinstance(item, (dict, list)) else scalar(item)
                yield f'{prefix}{name}: {text}'
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                yield f'{prefix}-'
                yield from lines(item, indent + 2)
            else:
                yield f'{prefix}- {scalar(item)}'


def scalar(value):
    if value is None:
        return 'unknown'
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    return str(value)


def next_command(command, context):
    """Keep selected paths in shell-safe commands printed for the next action."""
    words = shlex.split(command)
    if not context or not words or words[0] != 'crew' or words[1:] == ['--help']:
        return command
    selection = []
    for flag, key in (('--global-dir', 'global_dir'), ('--project', 'project')):
        if context.get(key) is not None:
            selection.extend([flag, context[key]])
    return shlex.join(['crew', *selection, *words[1:]])



def counts(records):
    return ', '.join(f'{status}: {count}' for status, count in sorted(
        Counter(record.get('status', 'unknown') for record in records).items())) or 'none'


def compact(record):
    return '; '.join(f'{label(key)}: {scalar(value)}' for key, value in record.items())


def warnings(record):
    result = {key: value for key, value in record.items()
              if (key.endswith('_error') or key in ('reason', 'reconciliation_needed',
                  'close_identity_changed', 'pause_requested', 'stop_requested')) and value}
    if record.get('launch_intent') and record.get('status') not in ('running', 'done', 'failed', 'paused'):
        result['launch'] = 'pending or uncertain; inspect details before retry'
    if record.get('close_intent') and not record.get('closed'):
        result['close'] = 'pending or uncertain; resource retained'
    return result


def task_lines(task_id, record, *, paths=False):
    selected = {key: record[key] for key in ('status', 'team', 'role', 'pane_id', 'closed', 'exit_code') if key in record}
    yield f'  {task_id}: {compact(selected)}'
    caution = warnings(record)
    if caution:
        yield '    ' + compact(caution)
    if paths:
        for key in ('artifact', 'launch_cwd'):
            if key in record:
                yield f'    {label(key)}: {record[key]}'
    if record.get('status') in ('blocked', 'failed') and record.get('output'):
        yield '    Output: ' + record['output']


def needs_detail(record):
    return (record.get('status') != 'done' or warnings(record)
            or (record.get('pane_id') or record.get('launch_intent')) and not record.get('closed'))


def cleanup_lines(cleanup):
    if not cleanup:
        yield 'Cleanup: not checked'
        return
    yield 'Cleanup:'
    for key, value in cleanup.items():
        if isinstance(value, list):
            if not value:
                continue
            # Removed worktrees have no remaining risk; their complete records stay in details.
            if key == 'worktrees':
                removed = sum(item.get('status') == 'removed' for item in value)
                if removed:
                    yield f'  Worktrees removed: {removed}'
                value = [item for item in value if item.get('status') != 'removed']
            if not value:
                continue
            yield f'  {label(key)}:'
            for item in value:
                yield '    - ' + (compact(item) if isinstance(item, dict) else scalar(item))
        elif isinstance(value, dict):
            if key == 'orchestrator':
                yield '  Orchestrator: ' + compact(value)
            else:
                yield from lines({key: value}, 2)
        else:
            yield f'  {label(key)}: {scalar(value)}'


def runtime_lines(result, command, context):
    run_id = result.get('run_id') or (context or {}).get('run')
    if run_id:
        yield f'Run: {run_id}'
    for key in ('flow', 'project', 'workspace'):
        if key in result:
            yield f'{label(key)}: {scalar(result[key])}'
    for key in ('start_uncertain', 'pause_requested', 'stop_requested', 'close_requested'):
        if result.get(key):
            yield f'{label(key)}: yes'
    if result.get('start_intent') and not result.get('workspace'):
        yield 'Start: pending or uncertain; inspect details before retry'
    if result.get('orchestrator'):
        orch = result['orchestrator']
        yield 'Orchestrator: ' + compact({key: orch[key] for key in ('status', 'pane_id', 'closed') if key in orch})
        if warnings(orch):
            yield '  ' + compact(warnings(orch))
    requests = result.get('control_requests', {})
    if requests:
        yield 'Control requests:'
        for team, request in requests.items():
            yield f'  {team}: {compact(request)}'
    if 'tasks' in result:
        tasks = result['tasks']
        entries = tasks.items() if isinstance(tasks, dict) else ((task['id'], task) for task in tasks)
        entries = list(entries)
        yield 'Tasks: ' + counts(task for _, task in entries)
        for task_id, task in entries:
            if needs_detail(task):
                yield from task_lines(task_id, task)
    if 'flows' in result:
        yield from lines({'flows': result['flows']})
    if 'decisions' in result:
        decisions = result['decisions']
        yield 'Decisions: ' + counts(decisions.values())
        for decision_id, decision in decisions.items():
            if decision.get('status') != 'approved' or decision_id == (context or {}).get('decision'):
                yield f'  {decision_id}:'
                selected = {key: decision[key] for key in ('status', 'kind', 'question', 'options', 'choice') if key in decision}
                yield from lines(selected, 4)
    if result.get('owned_panes'):
        yield 'Owned panes still recorded: ' + ', '.join(result['owned_panes'])
    yield from cleanup_lines(result.get('cleanup', {}))
    for lane in result.get('lanes', []):
        for tree in lane.get('worktrees', []):
            if tree.get('status') in ('creating', 'uncertain') or tree.get('cleanup_status') == 'removing':
                yield 'Worktree: ' + compact({key: tree[key] for key in ('path', 'status', 'cleanup_status', 'branch') if key in tree})
    if run_id:
        yield 'Details: ' + next_command('crew state show ' + shlex.quote(run_id) + ' --full', context)
    else:
        yield 'Details: use --toon for structured output'

def render(result, *, command=None, operation=None, context=None):
    """Render command outcomes without implying external completion."""
    if command == 'project' and 'error' not in result:
        project = Path(result['project'])
        return '\n'.join(['Crew project initialized', f'Project path: {project}',
                          f'Configuration: {project / ".crew/project.yaml"}',
                          f'Templates: {len(result["copied"])} files copied',
                          'Existing files were preserved',
                          'Review: The project overlay',
                          'Next: ' + next_command('crew config validate', context)])
    headings = {
        ('config', 'validate'): 'Configuration is valid',
        ('config', 'set'): 'Configuration updated',
        ('run', 'new'): 'Run created',
        ('run', 'start'): 'Run start result',
        ('dispatch', None): 'Task dispatch result',
        ('tick', None): 'Run tick result',
        ('status', None): 'Run status',
        ('report', None): 'Run report',
        ('resources', None): 'Resource readiness',
        ('doctor', None): 'Prerequisite checks',
        ('axi', 'check'): 'Tool availability',
        ('axi', 'install'): 'Tool installation requires user approval; no installer executed',
    }
    heading = headings.get((command, operation))
    if command in ('pause', 'resume', 'stop', 'close'):
        heading = f'{command.capitalize()} requested; the orchestrator must execute the request'
    data = dict(result)
    if 'help' in data:
        data['help'] = [next_command(item, context) for item in data['help']]
    if command == 'run' and operation == 'new' and 'error' not in result:
        data = {key: value for key, value in data.items() if key != 'config'}
    if 'error' in result:
        heading = None
    elif command == 'dispatch':
        task_id = (context or {}).get('task', 'Task')
        body = list(task_lines(task_id, result, paths=True))
        run_id = (context or {}).get('run')
        body.append('Details: ' + next_command('crew state show ' + shlex.quote(run_id) + ' --full', context)
                    if run_id else 'Details: use --toon for structured output')
        return '\n'.join(([heading] if heading else []) + body)
    elif command in ('status', 'report', 'tick', 'decide', 'pause', 'resume', 'stop', 'close') or (command == 'run' and operation == 'start'):
        return '\n'.join(([heading] if heading else []) + list(runtime_lines(result, command, context)))
    return '\n'.join(([heading] if heading else []) + list(lines(data)))
