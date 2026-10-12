"""Argparse interface with TOON defaults and opt-in human presentation."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

from pydantic import ValidationError
from crew.config import load_config
from crew.herdr import HerdrError
from crew.resources import check_resources
from crew.runtime import Runtime, config_set, create_run, identifier, init_project, project_config, safe_child
from crew.toon import encode
from crew.human import render

VERSION = '0.1.3'


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


class UsageError(ValueError):
    pass


def parser():
    common = Parser(add_help=False)
    for flag in ('global-dir', 'project', 'fields'):
        common.add_argument('--' + flag, default=argparse.SUPPRESS)
    for flag in ('full', 'toon', 'human'):
        common.add_argument('--' + flag, action='store_true', default=argparse.SUPPRESS)
    root = Parser(prog='crew', description='Manage config-driven local agent teams.', parents=[common])
    root.add_argument('-v', '-V', '--version', action='version', version=VERSION)
    commands = root.add_subparsers(dest='command')
    def add(parent, name, help_text):
        return parent.add_parser(name, help=help_text, description=help_text, parents=[common])
    project = add(commands, 'project', 'Initialize project configuration without overwriting files.')
    add(project.add_subparsers(dest='operation', required=True), 'init', 'Copy defaults and prompts into project .crew.')
    config = add(commands, 'config', 'Inspect or validate configuration.')
    operations = config.add_subparsers(dest='operation', required=True)
    for name in ('show', 'validate', 'path'):
        p = add(operations, name, name.capitalize() + ' configuration.')
        p.add_argument('--run')
    p = add(operations, 'set', 'Validate and set dot.path=YAML-value.')
    p.add_argument('assignment'); p.add_argument('--scope', choices=['global', 'project', 'run'], default='project')
    p.add_argument('--run')
    run = add(commands, 'run', 'Create and start frozen runs.')
    operations = run.add_subparsers(dest='operation', required=True)
    p = add(operations, 'new', 'Freeze config and initialize disjoint ticket lanes.')
    p.add_argument('--id', default=None); p.add_argument('--flow', required=True)
    p.add_argument('--items', required=True); p.add_argument('--groups', type=int, default=1)
    p = add(operations, 'start', 'Create workspace and launch configured orchestrator once.')
    p.add_argument('run')
    for name in ('status', 'report', 'tick'):
        p = add(commands, name, name.capitalize() + ' a run. Tick requires orchestrator role.')
        p.add_argument('run')
    state = add(commands, 'state', 'Read authoritative run state.')
    p = add(state.add_subparsers(dest='operation', required=True), 'show', 'Show authoritative run state.')
    p.add_argument('run')
    p = add(commands, 'dispatch', 'Dispatch one queued task once; orchestrator only.')
    p.add_argument('run'); p.add_argument('task')
    p = add(commands, 'decide', 'Resolve an existing exact decision explicitly.')
    p.add_argument('run'); p.add_argument('decision'); p.add_argument('choice')
    p = add(commands, 'ask', 'Record a user question; orchestrator only.')
    p.add_argument('run'); p.add_argument('question'); p.add_argument('--options', required=True)
    for name in ('pause', 'resume', 'stop', 'close'):
        p = add(commands, name, 'Record ' + name + ' intent for orchestrator execution.')
        p.add_argument('run'); p.add_argument('--team')
        if name == 'close': p.add_argument('--keep-worktrees', action='store_true')
    p = add(commands, 'resources', 'Inspect memory, load, workers and model quota.')
    p.add_argument('--run')
    add(commands, 'doctor', 'Read prerequisite availability; do not modify integration.')
    update = add(commands, 'update', 'Update the installed working checkout from a published stable release.')
    update.add_argument('--check', action='store_true', help='Read release availability without update changes.')
    axi = add(commands, 'axi', 'Inspect configured tool availability and installation decisions.')
    operations = axi.add_subparsers(dest='operation', required=True)
    for name in ('check', 'install'):
        p = add(operations, name, 'Report tools; installation always requires separate explicit user approval.')
        p.add_argument('--run')
    return root


def run_path(project, run):
    return safe_child(project, f'.crew/runs/{identifier(run)}')


def execute(args):
    global_dir = Path(getattr(args, 'global_dir', Path.home() / '.config/crew')).absolute()
    project = Path(getattr(args, 'project', Path.cwd())).resolve()
    command = args.command
    if command is None:
        directory = safe_child(project, '.crew/runs')
        runs = []
        if directory.exists():
            for path in sorted(directory.iterdir()):
                if path.is_dir() and not path.is_symlink() and not path.name.startswith('.'):
                    try:
                        state = Runtime(path).store.read_state()
                        runs.append({'id': path.name, 'flow': state['flow'], 'tasks': len(state['tasks'])})
                    except (OSError, ValueError):
                        runs.append({'id': path.name, 'flow': 'unverified', 'tasks': 0})
        return {'bin': str(Path(sys.argv[0]).absolute()), 'description': 'Local config-driven agent team runner',
                'runs': runs, 'count': len(runs), 'help': ['crew run new --id <run> --flow <flow> --items <item>',
                                                         'crew status <run>', 'crew project init']}
    if command == 'project':
        return init_project(global_dir, project)
    if command == 'run' and args.operation == 'new':
        args.id = args.id or ('run-' + uuid4().hex[:12])
        path = create_run(global_dir, project, args.id, args.flow, args.items.split(','), groups=args.groups)
        return {'run_id': args.id, 'path': str(path), 'status': 'created',
                'config': Runtime(path).config.model_dump(by_alias=True), 'help': [f'crew run start {args.id}']}
    if command == 'config':
        if args.operation == 'set':
            return config_set(global_dir, project, args.assignment, scope=args.scope, run_id=args.run)
        if args.operation == 'path':
            result = {'global': str(global_dir), 'project': str(project / '.crew/project.yaml')}
            if args.run: result['run'] = str(run_path(project, args.run) / 'run.yaml')
            return result
        config = Runtime(run_path(project, args.run)).config if args.run else project_config(global_dir, project)
        if args.operation == 'validate': return {'status': 'valid'}
        return config.model_dump(by_alias=True)
    if command in ('axi', 'resources', 'doctor'):
        run = getattr(args, 'run', None)
        runtime = Runtime(run_path(project, run)) if run else None
        config = runtime.config if runtime else project_config(global_dir, project)
        if command == 'axi':
            tools = [{'name': name, 'bin': tool.bin, 'available': shutil.which(tool.bin) is not None,
                      'install': tool.install, 'status': 'pending user approval' if args.operation == 'install' else 'checked'}
                     for name, tool in sorted(config.axis.items())]
            return {'tools': tools, 'status': 'pending user approval; no installer executed' if args.operation == 'install' else 'checked'}
        if command == 'resources':
            tasks = runtime.store.read_state()['tasks'] if runtime else {}
            active = sum(t['status'] not in ('done', 'failed', 'paused') and (t['status'] == 'running' or 'launch_intent' in t)
                         for t in tasks.values())
            return {'models': [{'model': model, **asdict(check_resources(config.resources, model, active_workers=active))}
                               for model in sorted({role.model for role in config.roles.values()})]}
        tools = [{'bin': binary, 'available': shutil.which(binary) is not None}
                 for binary in sorted({'herdr', *(role.cli for role in config.roles.values())})]
        integration = 'unknown'
        if shutil.which('herdr'):
            try:
                result = subprocess.run(['herdr', '--session', config.herdr_session, 'integration', 'status'],
                                        capture_output=True, text=True, check=False, timeout=5)
                integration = 'probe succeeded; restore setting and per-kind readiness require inspection' if result.returncode == 0 else 'probe failed'
            except (OSError, subprocess.SubprocessError):
                integration = 'probe unavailable'
        return {'config': 'valid', 'tools': tools, 'integration': integration,
                'restore_agents': 'unknown; not modified', 'live_smoke': 'unverified'}
    runtime = Runtime(run_path(project, args.run))
    if command in ('status', 'state', 'report'):
        state = runtime.store.read_state()
        if getattr(args, 'full', False) or command == 'state': return state
        return {'run_id': state['run_id'], 'flow': state['flow'], 'workspace': state.get('workspace'),
                'tasks': [{'id': key, 'status': task['status'], 'team': task['team'], 'role': task['role']}
                          for key, task in state['tasks'].items()],
                'decisions': state['decisions'], 'control_requests': state.get('control_requests', {}),
                'cleanup': state.get('cleanup', {}),
                'help': [f'crew state show {state["run_id"]} --full']}
    if command == 'run': return runtime.start()
    if command == 'tick': return runtime.tick()
    if command == 'dispatch': return runtime.dispatch(args.task)
    if command == 'decide': return runtime.decide(args.decision, args.choice)
    if command == 'ask': return runtime.ask(args.question, args.options.split(','))
    return runtime.request_control(command, team=args.team, keep_worktrees=getattr(args, 'keep_worktrees', False))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv in (['-v'], ['-V'], ['--version']):
        print(VERSION); return 0
    human = '--human' in argv and '--toon' not in argv
    args = None
    def output(result):
        detailed = args is not None and (
            getattr(args, 'fields', None) or getattr(args, 'full', False)
            or args.command == 'state'
            or (args.command == 'config' and args.operation == 'show'))
        print(render(result, command=getattr(args, 'command', None),
                     operation=getattr(args, 'operation', None),
                     context={'global_dir': str(Path(getattr(args, 'global_dir', Path.home() / '.config/crew')).absolute()),
                              'project': str(Path(getattr(args, 'project', Path.cwd())).resolve()),
                              'run': getattr(args, 'run', None), 'task': getattr(args, 'task', None),
                              'decision': getattr(args, 'decision', None)}) if human and not detailed else encode(result))
    try:
        args = parser().parse_args(argv)
        if getattr(args, 'human', False) and getattr(args, 'toon', False):
            raise UsageError('--human and --toon are mutually exclusive')
        if getattr(args, 'fields', None) and args.command not in (None, 'status', 'state', 'report', 'config', 'resources', 'doctor', 'axi'):
            raise UsageError('--fields is supported only on read commands')
        if getattr(args, 'fields', None) and args.command == 'config' and args.operation == 'set':
            raise UsageError('--fields is supported only on read commands')
        execution_args = args
        if human and args.command in ('status', 'report') and not getattr(args, 'fields', None):
            # Read authoritative task warnings for the human summary without changing TOON views.
            execution_args = argparse.Namespace(**{**vars(args), 'full': True})
        return_code = 0
        if args.command == 'update':
            from crew.update import run_update
            result, return_code = run_update(check=args.check)
        else:
            result = execute(execution_args)
        fields = getattr(args, 'fields', None)
        if fields:
            names = fields.split(',')
            if any(name not in result for name in names):
                raise UsageError('unknown output field; use --full to inspect available fields')
            result = {name: result[name] for name in names}
        output(result)
        return return_code
    except UsageError as error:
        output({'error': str(error), 'help': ['crew --help']}); return 2
    except ValidationError as error:
        locations = ['.'.join(map(str, item['loc'])) + ': ' + item['msg'] for item in error.errors(include_input=False)]
        output({'error': 'invalid configuration', 'fields': locations, 'help': ['crew config validate']}); return 1
    except (OSError, ValueError, TypeError, KeyError, PermissionError, HerdrError) as error:
        output({'error': str(error), 'help': ['crew config validate', 'crew status <run> --full']}); return 1


if __name__ == '__main__':
    raise SystemExit(main())
