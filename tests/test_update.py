"""Self-update contracts use temporary Git/install targets and fake network replies."""
import importlib
import importlib.util
import io
from pathlib import Path
import pytest
from unittest.mock import patch
from crew.cli import main


def updater():
    assert importlib.util.find_spec('crew.update') is not None, 'explicit updater module is missing'
    return importlib.import_module('crew.update')


def release_payload(tag='v0.1.1'):
    return {'id': 1, 'tag_name': tag, 'draft': False, 'prerelease': False,
            'published_at': '2026-10-11T00:00:00Z',
            'url': 'https://api.github.com/repos/Taimoorkhan1122/crew/releases/1',
            'html_url': f'https://github.com/Taimoorkhan1122/crew/releases/tag/{tag}',
            'tarball_url': f'https://api.github.com/repos/Taimoorkhan1122/crew/tarball/{tag}'}


def test_cli_explicit_update_check_refuses_development_without_network(monkeypatch):
    output = io.StringIO()
    with patch('sys.stdout', output):
        code = main(['update', '--check'])
    assert code == 1
    assert 'development' in output.getvalue() or 'editable' in output.getvalue()
    assert 'error:' in output.getvalue()


def test_release_resolves_numeric_published_tag_to_commit(monkeypatch):
    module = updater()
    commit = 'a' * 40
    replies = [release_payload(), {'ref': 'refs/tags/v0.1.1', 'object': {'type': 'commit', 'sha': commit}}]
    monkeypatch.setattr(module, 'read_json', lambda url: replies.pop(0))
    result = module.latest_release()
    assert result.version == '0.1.1'
    assert result.tag == 'v0.1.1' and result.commit == commit
    assert replies == []


@pytest.mark.parametrize('change', [{'draft': True}, {'prerelease': True}, {'tag_name': 'main'},
                                    {'tag_name': 'v1.2.3;echo bad'}, {'tag_name': 'v1.2.3-rc1'},
                                    {'tag_name': 'v01.2.3'}, {'tarball_url': 'https://example.com/archive'},
                                    {'published_at': None}, {'id': True}])
def test_unsafe_release_metadata_is_refused_before_commit_lookup(monkeypatch, change):
    module = updater()
    payload = {**release_payload(), **change}
    monkeypatch.setattr(module, 'read_json', lambda url: payload)
    with pytest.raises(module.UpdateError):
        module.latest_release()


def test_no_published_release_is_distinct_from_success(monkeypatch):
    module = updater()
    def missing(url):
        raise module.NoRelease('No published stable release is available')
    monkeypatch.setattr(module, 'read_json', missing)
    with pytest.raises(module.NoRelease):
        module.latest_release()


@pytest.fixture(scope='session')
def offline_wheels(tmp_path_factory):
    import os
    from update_helpers import wheelhouse
    backend = os.environ.get('CREW_TEST_SETUPTOOLS')
    if not backend:
        pytest.skip('offline install tests require CREW_TEST_SETUPTOOLS or a prepared backend cache')
    return wheelhouse(tmp_path_factory.mktemp('updater-wheelhouse') / 'wheels', backend)


@pytest.fixture
def local_install(tmp_path, offline_wheels):
    from update_helpers import make_install
    return make_install(tmp_path, offline_wheels)


def test_installed_preflight_supports_ignored_configuration_and_generated_files(local_install):
    import json
    from update_helpers import installed_call
    source = local_install['source']
    (source / '.DS_Store').write_bytes(b'local Finder data')
    (source / '.crew/runs/active').mkdir(parents=True)
    sentinel = source / '.crew/runs/active/state.json'
    sentinel.write_text('preserve run state')
    result = installed_call(local_install, "import crew.update as u,json; t=u.discover(); s=u.preflight(t); print(json.dumps({'source':str(t.source),'environment':str(t.environment),'version':t.version,'head':s['head']}))")
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data == {'source': str(source), 'environment': str(local_install['environment']), 'version': '0.1.0', 'head': local_install['old']}
    assert sentinel.read_text() == 'preserve run state'


def test_installed_preflight_refuses_unsafe_checkout_and_metadata(local_install):
    import json
    from update_helpers import installed_call, run
    source = local_install['source']
    code = "import crew.update as u; u.preflight(u.discover())"
    (source / 'unexpected.txt').write_text('user content')
    result = installed_call(local_install, code)
    assert result.returncode != 0 and 'untracked' in result.stderr
    (source / 'unexpected.txt').unlink()
    file = source / 'crew/cli.py'
    before = file.read_bytes()
    file.write_bytes(before + b'\n# user edit\n')
    result = installed_call(local_install, code)
    assert result.returncode != 0 and 'dirty' in result.stderr
    assert file.read_bytes() == before + b'\n# user edit\n'
    file.write_bytes(before)
    run(['git', '-C', source, 'remote', 'set-url', 'origin', 'https://example.com/crew.git'], cwd=source.parent)
    result = installed_call(local_install, code)
    assert result.returncode != 0 and 'origin' in result.stderr
    run(['git', '-C', source, 'remote', 'set-url', 'origin', 'https://github.com/Taimoorkhan1122/crew.git'], cwd=source.parent)
    metadata = next(local_install['environment'].glob('lib/python*/site-packages/crew_runner-*.dist-info/direct_url.json'))
    original = metadata.read_text()
    metadata.write_text(json.dumps({'url': source.as_uri(), 'dir_info': {'editable': True}}))
    result = installed_call(local_install, code)
    assert result.returncode != 0 and 'editable' in result.stderr
    metadata.write_text(original)
    alias = source.parent / 'source alias'
    alias.symlink_to(source, target_is_directory=True)
    metadata.write_text(json.dumps({'url': alias.as_uri(), 'dir_info': {}}))
    result = installed_call(local_install, code)
    assert result.returncode != 0 and 'symlink' in result.stderr
    metadata.write_text(original)
    metadata.write_text(json.dumps({'url': source.as_uri(), 'archive_info': {}}))
    result = installed_call(local_install, code)
    assert result.returncode != 0 and 'local-directory' in result.stderr
    metadata.write_text(original)


def test_check_is_readonly_and_uses_install_targets_not_cwd_or_project(local_install, tmp_path):
    import json
    from update_helpers import installed_call
    module = updater()
    elsewhere = tmp_path / 'another project'
    elsewhere.mkdir()
    (elsewhere / 'crew.py').write_text("raise RuntimeError('caller source imported')")
    code = ("import crew.update as u,json; "
            f"u.latest_release=lambda:u.Release('v0.1.1','0.1.1',{local_install['commit']!r}); "
            "print(json.dumps(u.run_update(check=True)))")
    source = local_install['source']
    before = {str(path.relative_to(source)): path.read_bytes() for path in source.rglob('*') if path.is_file()}
    result = installed_call(local_install, code, cwd=elsewhere)
    assert result.returncode == 0, result.stdout + result.stderr
    data, status = json.loads(result.stdout)
    assert status == 0 and data['status'] == 'available' and data['check'] is True
    assert data['source'] == str(source) and data['environment'] == str(local_install['environment'])
    assert {str(path.relative_to(source)): path.read_bytes() for path in source.rglob('*') if path.is_file()} == before
    assert not (local_install['environment'].parent / '.crew-updates').exists()


def test_same_older_and_missing_release_never_install(local_install):
    import json
    from update_helpers import installed_call
    common = 'import crew.update as u,json; '
    same = installed_call(local_install, common + f"u.latest_release=lambda:u.Release('v0.1.0','0.1.0',{local_install['old']!r}); print(json.dumps(u.run_update()))")
    assert same.returncode == 0, same.stderr
    data, code = json.loads(same.stdout)
    assert code == 0 and data['status'] == 'up_to_date'
    older = installed_call(local_install, common + f"u.latest_release=lambda:u.Release('v0.0.9','0.0.9',{local_install['old']!r}); print(json.dumps(u.run_update()))")
    data, code = json.loads(older.stdout)
    assert code == 1 and data['status'] == 'refused' and 'downgrade' in data['error']
    moved = installed_call(local_install, common + f"u.latest_release=lambda:u.Release('v0.1.0','0.1.0',{local_install['commit']!r}); print(json.dumps(u.run_update()))")
    data, code = json.loads(moved.stdout)
    assert code == 1 and 'commit' in data['error']
    absent = installed_call(local_install, common + "def_missing=None\ndef missing(): raise u.NoRelease('no stable release')\nu.latest_release=missing\nprint(json.dumps(u.run_update()))")
    data, code = json.loads(absent.stdout)
    assert code == 1 and data['status'] == 'no_release'
    assert not (local_install['environment'].parent / '.crew-updates').exists()


def update_code(fixture, extra=''):
    """Only release lookup and network transport are replaced; Git/build/pip stay real."""
    return ("import crew.update as u,json\n"
            f"u.latest_release=lambda:u.Release('v0.1.1','0.1.1',{fixture['commit']!r})\n"
            f"def network(release,destination): u.command(['git','clone','--no-checkout',{str(fixture['publisher'])!r},str(destination)],cwd=destination.parent)\n"
            "u.download=network\n" + extra + "\nprint(json.dumps(u.run_update()))\n")


def test_successful_update_pins_release_preserves_ignored_state_and_remains_repeatable(local_install, tmp_path):
    import json
    import os
    import shutil
    import subprocess
    from update_helpers import installed_call, run
    source = local_install['source']
    (source / '.crew/runs/demo').mkdir(parents=True)
    sentinel = source / '.crew/runs/demo/state.json'
    sentinel.write_text('old run remains')
    (source / '.env').write_text('private local configuration')
    (source / '.DS_Store').write_bytes(b'Finder content')
    project = tmp_path / 'unrelated project'
    project.mkdir()
    (project / '.crew').mkdir()
    (project / '.crew/project.yaml').write_text('global and project remain')
    result = installed_call(local_install, update_code(local_install), cwd=project)
    assert result.returncode == 0, result.stdout + result.stderr
    data, code = json.loads(result.stdout)
    assert code == 0 and data['status'] == 'updated', data
    assert data['installed_version'] == '0.1.1' and data['release_commit'] == local_install['commit']
    assert run(['git', '-C', source, 'rev-parse', 'HEAD'], cwd=tmp_path) == local_install['commit']
    assert sentinel.read_text() == 'old run remains'
    assert (source / '.env').read_text() == 'private local configuration'
    assert (source / '.DS_Store').read_bytes() == b'Finder content'
    assert (project / '.crew/project.yaml').read_text() == 'global and project remain'
    environment = local_install['environment']
    assert run([environment / 'bin/crew', '--version'], cwd=project, env=local_install['env']) == '0.1.1'
    assert 'update' in run([environment / 'bin/crew', '--help'], cwd=project, env=local_install['env'])
    metadata = next(environment.glob('lib/python*/site-packages/crew_runner-*.dist-info/direct_url.json'))
    assert json.loads(metadata.read_text()) == {'dir_info': {}, 'url': source.as_uri()}
    assert '# release 0.1.1' in (environment / 'share/crew/config.yaml').read_text()
    # The working environment must not depend on rehearsal/downloaded files or their shebangs.
    recovery = Path(data['recovery'])
    for name in ('trial', 'downloaded', 'wheels'):
        path = recovery / name
        if path.exists():
            shutil.rmtree(path)
    assert run([environment / 'bin/crew', '--version'], cwd=project, env=local_install['env']) == '0.1.1'
    repeat = installed_call(local_install, update_code(local_install), cwd=project)
    repeated, code = json.loads(repeat.stdout)
    assert code == 0 and repeated['status'] == 'up_to_date'


def test_prepare_failure_does_not_change_source_or_environment(local_install):
    import json
    from update_helpers import installed_call, run
    source = local_install['source']
    (local_install['publisher'] / 'crew/cli.py').write_text("VERSION = '9.9.9'\n")
    # Release commit/version checks must inspect the fetched pinned content, not a caller constant.
    run(['git', '-C', local_install['publisher'], 'add', '.'], cwd=source.parent)
    run(['git', '-C', local_install['publisher'], 'commit', '-m', 'invalid release version'], cwd=source.parent)
    changed = run(['git', '-C', local_install['publisher'], 'rev-parse', 'HEAD'], cwd=source.parent)
    run(['git', '-C', local_install['publisher'], 'tag', '-f', 'v0.1.1'], cwd=source.parent)
    local_install['commit'] = changed
    result = installed_call(local_install, update_code(local_install))
    data, code = json.loads(result.stdout)
    assert code == 1 and data['status'] != 'updated'
    assert 'version' in data['error']
    assert run(['git', '-C', source, 'rev-parse', 'HEAD'], cwd=source.parent) == local_install['old']
    assert run([local_install['environment'] / 'bin/crew', '--version'], cwd=source.parent, env=local_install['env']) == '0.1.0'


def test_concurrent_checkout_change_is_retained_before_replacement(local_install):
    import json
    from update_helpers import installed_call, run
    source = local_install['source']
    extra = ("real_prepare=u.prepare\n"
             "def prepare(*args):\n"
             "    result=real_prepare(*args)\n"
             f"    from pathlib import Path; Path({str(source / 'concurrent.txt')!r}).write_text('user change')\n"
             "    return result\n"
             "u.prepare=prepare\n")
    result = installed_call(local_install, update_code(local_install, extra))
    data, code = json.loads(result.stdout)
    assert code == 1 and data['status'] != 'updated'
    assert 'changed' in data['error'] or 'untracked' in data['error']
    assert (source / 'concurrent.txt').read_text() == 'user change'
    assert run(['git', '-C', source, 'rev-parse', 'HEAD'], cwd=source.parent) == local_install['old']
    assert run([local_install['environment'] / 'bin/crew', '--version'], cwd=source.parent, env=local_install['env']) == '0.1.0'


def test_post_mutation_failure_keeps_full_recovery_and_commands_restore_usable_install(local_install):
    import json
    import subprocess
    from update_helpers import installed_call, run
    environment = local_install['environment']
    extra = ("def install(targets,work):\n"
             f"    from pathlib import Path; Path({str(environment / 'partial-dependency-change')!r}).write_text('partial install')\n"
             "    raise u.UpdateError('installer failed after dependency mutation')\n"
             "u.install=install\n")
    result = installed_call(local_install, update_code(local_install, extra))
    data, code = json.loads(result.stdout)
    assert code == 1 and data['status'] == 'recovery_required', data
    assert 'dependency' in data['error'] and data['recovery']
    recovery = Path(data['recovery'])
    assert (recovery / 'old_source/.git').is_dir()
    assert (recovery / 'old_environment/bin/crew').is_file()
    assert (environment / 'partial-dependency-change').read_text() == 'partial install'
    for command in data['recovery_commands']:
        restored = subprocess.run(command, shell=True, cwd=recovery.parent, env=local_install['env'],
                                  capture_output=True, text=True, timeout=30)
        assert restored.returncode == 0, restored.stdout + restored.stderr
    assert not (environment / 'partial-dependency-change').exists()
    assert run([environment / 'bin/crew', '--version'], cwd=recovery.parent, env=local_install['env']) == '0.1.0'
    assert 'update' in run([environment / 'bin/crew', '--help'], cwd=recovery.parent, env=local_install['env'])


def test_updater_lock_is_external_and_second_process_cannot_acquire_it(local_install):
    import json
    import subprocess
    from update_helpers import installed_call
    result = installed_call(local_install,
        "import crew.update as u,subprocess,sys,json; t=u.discover()\n"
        "with u.update_lock(t):\n"
        "    other=subprocess.run([sys.executable,'-I','-B','-c',\"import crew.update as u; t=u.discover(); ctx=u.update_lock(t); ctx.__enter__()\"],capture_output=True,text=True)\n"
        "    print(json.dumps({'code':other.returncode,'error':other.stderr}))\n")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data['code'] != 0 and 'another update' in data['error']
    assert (local_install['environment'].parent / '.crew-updates/update.lock').is_file()


def test_subprocess_environment_cannot_redirect_install_or_imports(monkeypatch):
    module = updater()
    for name in ('PIP_ROOT', 'PIP_LOG', 'PIP_TARGET', 'PIP_PREFIX', 'PIP_USER',
                 'PYTHONPATH', 'PYTHONHOME', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE',
                 'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0'):
        monkeypatch.setenv(name, '/unrelated user data')
    environment = module.isolated_env()
    for name in ('PIP_ROOT', 'PIP_LOG', 'PIP_TARGET', 'PIP_PREFIX', 'PIP_USER',
                 'PYTHONPATH', 'PYTHONHOME', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE',
                 'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0'):
        assert name not in environment
    assert environment['PIP_CONFIG_FILE'] == module.os.devnull


@pytest.mark.parametrize('fault,needle', [('moved_tag', 'moved'), ('ignored_collision', 'collides'),
                                        ('divergence', 'git'), ('release_symlink', 'symlink'), ('build', 'build'),
                                        ('dependencies', 'dependency'), ('verification', 'verification')])
def test_update_failure_boundaries_keep_originals_or_full_recovery(local_install, fault, needle):
    import json
    from update_helpers import installed_call, run
    source = local_install['source']
    publisher = local_install['publisher']
    extra = ''
    if fault == 'moved_tag':
        run(['git', '-C', publisher, 'tag', '-f', 'v0.1.1', local_install['old']], cwd=source.parent)
    elif fault == 'ignored_collision':
        (source / '.env').write_text('keep local secrets')
        (publisher / '.env').write_text('incoming collision')
        run(['git', '-C', publisher, 'add', '-f', '.env'], cwd=source.parent)
        run(['git', '-C', publisher, 'commit', '-m', 'collision'], cwd=source.parent)
        local_install['commit'] = run(['git', '-C', publisher, 'rev-parse', 'HEAD'], cwd=source.parent)
        run(['git', '-C', publisher, 'tag', '-f', 'v0.1.1'], cwd=source.parent)
    elif fault == 'release_symlink':
        (publisher / 'crew/unsafe_link').symlink_to(source.parent / 'outside source')
        run(['git', '-C', publisher, 'add', '.'], cwd=source.parent)
        run(['git', '-C', publisher, 'commit', '-m', 'unsafe symlink'], cwd=source.parent)
        local_install['commit'] = run(['git', '-C', publisher, 'rev-parse', 'HEAD'], cwd=source.parent)
        run(['git', '-C', publisher, 'tag', '-f', 'v0.1.1'], cwd=source.parent)
    elif fault == 'divergence':
        run(['git', '-C', source, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
             'commit', '--allow-empty', '-m', 'local divergence'], cwd=source.parent)
        local_install['old'] = run(['git', '-C', source, 'rev-parse', 'HEAD'], cwd=source.parent)
    elif fault in ('build', 'dependencies'):
        condition = "'wheel' in list(map(str,args))" if fault == 'build' else "any(str(arg).endswith('.whl') for arg in args)"
        extra = ("real_command=u.command\ndef command(args,**kwargs):\n"
                 f"    if {condition}: raise u.UpdateError('{needle} rehearsal failed')\n"
                 "    return real_command(args,**kwargs)\nu.command=command\n")
    else:
        extra = ("real_verify=u.verify_install\ndef verify(environment,version,work,source=None):\n"
                 "    if source is not None: raise u.UpdateError('final verification failed')\n"
                 "    return real_verify(environment,version,work,source)\nu.verify_install=verify\n")
    result = installed_call(local_install, update_code(local_install, extra))
    assert result.returncode == 0, result.stderr
    data, code = json.loads(result.stdout)
    assert code == 1 and needle in data['error'], data
    if fault == 'verification':
        assert data['status'] == 'recovery_required'
        assert (Path(data['recovery']) / 'old_environment/bin/crew').is_file()
        assert run(['git', '-C', Path(data['recovery']) / 'old_source', 'rev-parse', 'HEAD'], cwd=source.parent) == local_install['old']
    else:
        assert data['status'] == 'refused'
        assert run(['git', '-C', source, 'rev-parse', 'HEAD'], cwd=source.parent) == local_install['old']
        assert run([local_install['environment'] / 'bin/crew', '--version'], cwd=source.parent, env=local_install['env']) == '0.1.0'
    if fault == 'ignored_collision':
        assert (source / '.env').read_text() == 'keep local secrets'


def test_installed_cli_update_output_flags_and_import_isolation(local_install, tmp_path):
    from update_helpers import installed_call
    import json
    caller = tmp_path / 'decoy caller'
    caller.mkdir()
    (caller / 'crew.py').write_text("raise RuntimeError('caller crew imported')")
    (caller / 'pip.py').write_text("raise RuntimeError('caller pip imported')")
    code = ("import crew.update as u; "
            f"u.latest_release=lambda:u.Release('v0.1.1','0.1.1',{local_install['commit']!r}); "
            "from crew.cli import main; import sys; sys.exit(main(sys.argv[1:]))")
    base = ['update', '--check']
    default = installed_call(local_install, code, argv=base, cwd=caller)
    explicit = installed_call(local_install, code, argv=['--toon', *base], cwd=caller)
    human = installed_call(local_install, code, argv=[*base, '--human'], cwd=caller)
    assert default.returncode == explicit.returncode == human.returncode == 0
    assert default.stdout == explicit.stdout and 'status: available' in default.stdout
    assert 'available' in human.stdout and human.stdout != default.stdout
    conflict = installed_call(local_install, code, argv=['--human', *base, '--toon'], cwd=caller)
    assert conflict.returncode == 2 and 'mutually exclusive' in conflict.stdout
    assert not (local_install['environment'].parent / '.crew-updates').exists()


def test_source_execution_with_installed_metadata_still_refuses_update(local_install):
    import shutil
    for metadata in local_install['source'].glob('*.egg-info'):
        shutil.rmtree(metadata)
    from update_helpers import installed_call
    code = (f"import sys; sys.path.insert(0,{str(local_install['source'])!r}); "
            "import crew.update as u,json; u.latest_release=lambda: (_ for _ in ()).throw(u.UpdateError('unexpected release lookup')); print(json.dumps(u.run_update(check=True)))")
    result = installed_call(local_install, code)
    assert result.returncode == 0, result.stderr
    import json
    data, status = json.loads(result.stdout)
    assert status == 1 and 'development' in data['error'], data


def test_subprocess_timeout_is_bounded_with_descendants(tmp_path):
    import sys
    import time
    module = updater()
    started = time.monotonic()
    script = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(4)']); time.sleep(4)"
    with pytest.raises(module.UpdateError, match='timed out'):
        module.command([sys.executable, '-I', '-B', '-c', script], cwd=tmp_path, timeout=0.1)
    assert time.monotonic() - started < 2
    sentinel = tmp_path / 'descendant wrote after timeout'
    child = f'import time,pathlib; time.sleep(0.6); pathlib.Path({str(sentinel)!r}).write_text("unsafe continuing installer")'
    script = f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(4)'
    with pytest.raises(module.UpdateError):
        module.command([sys.executable, '-I', '-B', '-c', script], cwd=tmp_path, timeout=0.2)
    time.sleep(0.8)
    assert not sentinel.exists()


def test_subprocess_timeout_cleanup_is_bounded_with_detached_pipe_descendant(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import threading
    import time
    module = updater()
    token = 'crew-detached-writer-probe'
    pidfile = tmp_path / 'detached.pid'
    grandchild = ("import os,time\n"
                  f"open({str(pidfile)!r},'w').write(str(os.getpid()))\n"
                  f"marker={token!r}\n"
                  "time.sleep(30)\n")
    script = ("import subprocess,sys,time\n"
              f"subprocess.Popen([sys.executable,'-I','-B','-c',{grandchild!r}],start_new_session=True)\n"
              "time.sleep(30)\n")
    outcome = {}

    def invoke():
        try:
            module.command([sys.executable, '-I', '-B', '-c', script], cwd=tmp_path, timeout=1.0)
            outcome['raised'] = False
        except module.UpdateError as error:
            outcome['error'] = str(error)

    # The watchdog thread and the bounded join keep this test from hanging when cleanup regresses.
    worker = threading.Thread(target=invoke, daemon=True)
    started = time.monotonic()
    worker.start()
    worker.join(timeout=20)
    elapsed = time.monotonic() - started
    for _ in range(40):
        if pidfile.exists():
            break
        time.sleep(0.05)
    pid = int(pidfile.read_text()) if pidfile.exists() else None
    if pid is not None:
        # Stop only the exact fixture-owned writer that the probe created and identified.
        identity = subprocess.run(['ps', '-ww', '-o', 'command=', '-p', str(pid)],
                                  capture_output=True, text=True).stdout
        assert token in identity, identity
        os.kill(pid, signal.SIGKILL)
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.1)
    worker.join(timeout=5)
    assert not worker.is_alive(), 'updater timeout cleanup blocked on a detached inherited pipe'
    assert 'timed out' in outcome.get('error', ''), outcome
    assert elapsed < 8, elapsed


def test_first_source_rename_failure_keeps_exact_targets_without_recovery_commands(local_install):
    import json
    from update_helpers import installed_call
    source = local_install['source']
    extra = ("from pathlib import Path\n"
             "targets=u.discover(); before_source=u.snapshot(targets.source); before_environment=u.snapshot(targets.environment,allow_symlinks=True)\n"
             "real_rename=Path.rename\n"
             "def rename(path,destination):\n"
             f"    if path == Path({str(source)!r}): raise OSError(18,'cross-device link')\n"
             "    return real_rename(path,destination)\n"
             "Path.rename=rename\n"
             "real_run=u.run_update\n"
             "def run_update(*args,**kwargs):\n"
             "    result,code=real_run(*args,**kwargs)\n"
             "    result['targets_unchanged']=(u.snapshot(targets.source)==before_source and u.snapshot(targets.environment,allow_symlinks=True)==before_environment)\n"
             "    return result,code\n"
             "u.run_update=run_update\n")
    result = installed_call(local_install, update_code(local_install, extra))
    assert result.returncode == 0, result.stderr
    data, code = json.loads(result.stdout)
    assert code == 1 and data['targets_unchanged'] is True, data
    assert data['status'] == 'refused', data
    assert 'recovery_commands' not in data
    assert 'cross-device' in data['error']
    assert not (Path(data['recovery']) / 'old_source').exists()


def test_external_symlinked_package_root_refuses_without_touching_external_files(local_install):
    import json
    from update_helpers import installed_call
    environment = local_install['environment']
    packages = next(environment.glob('lib/python*/site-packages'))
    outside = environment.parent / 'external package root'
    packages.rename(outside)
    packages.symlink_to(outside, target_is_directory=True)
    sentinel = outside / 'external-user-config'
    sentinel.write_text('must remain unchanged')
    code = ("import crew.update as u,json\n"
            f"from pathlib import Path; outside=Path({str(outside)!r}); before=u.snapshot(outside,allow_symlinks=True)\n"
            f"u.latest_release=lambda:u.Release('v0.1.1','0.1.1',{local_install['commit']!r})\n"
            "result,status=u.run_update(check=True); result['outside_unchanged']=u.snapshot(outside,allow_symlinks=True)==before; print(json.dumps([result,status]))\n")
    result = installed_call(local_install, code)
    assert result.returncode == 0, result.stderr
    data, status = json.loads(result.stdout)
    assert data['outside_unchanged'] is True and sentinel.read_text() == 'must remain unchanged'
    assert status == 1 and data['status'] == 'refused', data
    assert 'external' in data['error'] or 'alias' in data['error']
    assert not (environment.parent / '.crew-updates').exists()


def test_malformed_installed_metadata_has_structured_cli_refusal(local_install):
    import json
    from update_helpers import installed_call
    metadata = next(local_install['environment'].glob('lib/python*/site-packages/crew_runner-*.dist-info/direct_url.json'))
    code = ("import crew.update as u,sys\n"
            "def network(): raise u.UpdateError('unexpected release lookup')\n"
            "u.latest_release=network\n"
            "from crew.cli import main; sys.exit(main(['update','--check']))")
    cases = [{'url': 42, 'dir_info': {}}, {'url': None, 'dir_info': {}},
             {'url': [], 'dir_info': {}}, {'url': {}, 'dir_info': {}}, [], None,
             {'url': local_install['source'].as_uri(), 'dir_info': []},
             {'url': local_install['source'].as_uri(), 'dir_info': {'editable': 'false'}}]
    for malformed in cases:
        metadata.write_text(json.dumps(malformed))
        result = installed_call(local_install, code)
        assert result.returncode == 1, (malformed,result.stdout,result.stderr)
        assert result.stderr == '', (malformed,result.stderr)
        assert 'status: refused' in result.stdout and 'error:' in result.stdout, (malformed,result.stdout)
        assert 'metadata' in result.stdout, (malformed,result.stdout)
        assert 'unexpected release lookup' not in result.stdout
    assert not (local_install['environment'].parent / '.crew-updates').exists()
