"""Guided installer contracts run the real script against disposable homes.

Real Git checkouts, virtual environments, pip, entrypoints, and fresh shells
are exercised. Only the package source boundary is bounded by a local offline
wheelhouse where a test needs an install to succeed without the network.
"""
import json
import os
from pathlib import Path
import stat
import sys

import pytest

from installer_helpers import (MARKER, SCRIPT, call_installer, install_env,
                               make_checkout, offline_pip, offline_without_wheels,
                               run, shell_finds_crew)

VERSION = '0.1.3'


@pytest.fixture(scope='session')
def offline_wheels(tmp_path_factory):
    import os as _os
    from update_helpers import wheelhouse
    backend = _os.environ.get('CREW_TEST_SETUPTOOLS')
    if not backend:
        pytest.skip('offline installer tests require CREW_TEST_SETUPTOOLS')
    return wheelhouse(tmp_path_factory.mktemp('installer-wheelhouse') / 'wheels', backend)


def marker_lines(path):
    return [line for line in path.read_text().splitlines() if line.startswith(MARKER)]


def write_shim(directory, name, body='exit 1\n'):
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / name
    script.write_text('#!/bin/sh\n' + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return directory


# --- Refusals that must happen before any write ---------------------------------

def test_help_prints_usage_and_writes_nothing(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    result = call_installer(home, args=('--help',))
    assert result.returncode == 0
    assert 'usage' in result.stdout.lower()
    assert list(home.iterdir()) == []


def test_unsupported_shell_refused_before_writes(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    venv = home / 'venv'
    bin_dir = home / '.local/bin'
    result = call_installer(home, shell='/bin/sh', args=(
        '--yes', '--source', str(source), '--venv', str(venv), '--bin-dir', str(bin_dir)))
    assert result.returncode != 0
    assert 'zsh' in result.stderr and 'bash' in result.stderr
    assert not venv.exists() and not bin_dir.exists()
    assert not (home / '.zshenv').exists() and not (home / '.bash_profile').exists()


def test_missing_python_refused_before_writes(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    shadow = write_shim(tmp_path / 'shim', 'python3')
    venv = home / 'venv'
    path = str(shadow) + os.pathsep + os.environ['PATH']
    result = call_installer(home, args=(
        '--yes', '--source', str(source), '--venv', str(venv)), path=path)
    assert result.returncode != 0
    assert 'python' in result.stderr.lower()
    assert not venv.exists()


def test_missing_git_refused_before_writes(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    shadow = write_shim(tmp_path / 'shim', 'git')
    venv = home / 'venv'
    path = str(shadow) + os.pathsep + os.environ['PATH']
    result = call_installer(home, args=(
        '--yes', '--source', str(source), '--venv', str(venv)), path=path)
    assert result.returncode != 0
    assert 'error:' in result.stderr
    assert not venv.exists()


def test_confirmation_required_without_yes_on_closed_input(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    venv = home / 'venv'
    result = call_installer(home, args=(
        '--source', str(source), '--venv', str(venv)), stdin='')
    assert result.returncode != 0
    assert '--yes' in result.stderr
    assert not venv.exists()


def test_source_without_official_origin_refused(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    run(['git', '-C', source, 'remote', 'set-url', 'origin', 'https://example.com/crew.git'],
        cwd=source.parent)
    venv = home / 'venv'
    result = call_installer(home, args=(
        '--yes', '--source', str(source), '--venv', str(venv)))
    assert result.returncode != 0
    assert 'origin' in result.stderr.lower()
    assert not venv.exists()


def test_source_that_is_not_a_checkout_refused(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    plain = tmp_path / 'plain'
    plain.mkdir()
    (plain / 'pyproject.toml').write_text('[project]\nname = "crew-runner"\nversion = "0.1.0"\n')
    venv = home / 'venv'
    result = call_installer(home, args=(
        '--yes', '--source', str(plain), '--venv', str(venv)))
    assert result.returncode != 0
    assert 'error:' in result.stderr
    assert not venv.exists()


def test_source_and_environment_overlap_refused(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    venv = source / 'env'
    result = call_installer(home, args=(
        '--yes', '--source', str(source), '--venv', str(venv)))
    assert result.returncode != 0
    assert 'overlap' in result.stderr.lower()
    assert not venv.exists()


def test_occupied_command_link_refused(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    bin_dir = home / '.local/bin'
    bin_dir.mkdir(parents=True)
    foreign = bin_dir / 'crew'
    foreign.write_text('foreign command\n')
    result = call_installer(home, args=(
        '--yes', '--source', str(source), '--bin-dir', str(bin_dir)))
    assert result.returncode != 0
    assert 'error:' in result.stderr
    assert foreign.read_text() == 'foreign command\n'
    assert not (home / '.local/share/crew/venv').exists()


def test_symlinked_environment_refused(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    real = tmp_path / 'real_env'
    real.mkdir()
    (home / '.local/share/crew').mkdir(parents=True)
    os.symlink(real, home / '.local/share/crew/venv')
    result = call_installer(home, args=('--yes', '--source', str(source)))
    assert result.returncode != 0
    assert 'symlink' in result.stderr.lower()
    assert list(real.iterdir()) == []


def test_symlinked_startup_file_refused_without_damage(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    real = home / 'realcfg'
    real.write_text('original settings\n')
    os.symlink(real, home / '.zshenv')
    result = call_installer(home, args=('--yes', '--source', str(source)))
    assert result.returncode != 0
    assert 'symlink' in result.stderr.lower()
    assert real.read_text() == 'original settings\n'
    assert not (home / '.local/share/crew/venv').exists()


def test_failed_installation_leaves_no_link_or_path(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    venv = home / '.local/share/crew/venv'
    bin_dir = home / '.local/bin'
    result = call_installer(home, args=('--yes', '--source', str(source)),
                            extra=offline_without_wheels())
    assert result.returncode != 0
    assert 'error:' in result.stderr
    assert not venv.exists()
    assert not (bin_dir / 'crew').exists()
    assert not (home / '.zshenv').exists()


@pytest.mark.parametrize('name', ['ven v $HOME', 'ven v `id`', 'ven v "q"'])
def test_unsafe_environment_path_refused_before_writes(tmp_path, name):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    venv = tmp_path / name
    result = call_installer(home, args=(
        '--yes', '--source', str(source), '--venv', str(venv)))
    assert result.returncode != 0
    assert 'error:' in result.stderr
    assert not venv.exists()
    assert not (home / '.zshenv').exists()


def test_symlinked_startup_file_with_marker_refused(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    original = MARKER + '\nexport PATH="$HOME/.local/bin:$PATH"\n'
    real = home / 'realcfg'
    real.write_text(original)
    os.symlink(real, home / '.zshenv')
    result = call_installer(home, args=('--yes', '--source', str(source)))
    assert result.returncode != 0
    assert 'symlink' in result.stderr.lower()
    assert real.read_text() == original


def test_unsafe_home_path_refused_before_writes(tmp_path):
    home = tmp_path / 'ho $me'
    home.mkdir()
    source = make_checkout(tmp_path)
    result = call_installer(home, args=('--yes', '--source', str(source)))
    assert result.returncode != 0
    assert 'error:' in result.stderr
    assert not (home / '.zshenv').exists()
    assert not (home / '.local/share/crew/venv').exists()


def test_ancestor_symlink_environment_refused(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    target = tmp_path / 'redirected'
    target.mkdir()
    link = home / 'linkdir'
    os.symlink(target, link)
    venv = link / 'venv'
    result = call_installer(home, args=(
        '--yes', '--source', str(source), '--venv', str(venv)))
    assert result.returncode != 0
    assert 'error:' in result.stderr
    assert list(target.iterdir()) == []


def test_dirty_source_refused_before_writes(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    edited = source / 'crew/cli.py'
    edited.write_text(edited.read_text() + '\n# local edit\n')
    venv = home / 'venv'
    result = call_installer(home, args=(
        '--yes', '--source', str(source), '--venv', str(venv)))
    assert result.returncode != 0
    assert 'error:' in result.stderr
    assert not venv.exists()
    assert not (home / '.zshenv').exists()


# --- Real offline installs ------------------------------------------------------

def installed_metadata(venv):
    return list(venv.glob('lib/python*/site-packages/crew_runner-*.dist-info'))[0]


def test_zsh_fresh_install_links_and_configures_path(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    venv = home / '.local/share/crew/venv'
    bin_dir = home / '.local/bin'
    # Simulate an activated shell: the bin directory is on the current PATH but
    # no startup file configures it. The installer must not trust this state.
    activated = str(bin_dir) + os.pathsep + os.environ['PATH']
    result = call_installer(home, shell='/bin/zsh', args=('--yes', '--source', str(source)),
                            path=activated, extra=offline_pip(offline_wheels))
    assert result.returncode == 0, result.stdout + result.stderr

    link = bin_dir / 'crew'
    assert link.is_symlink()
    assert os.path.realpath(link) == os.path.realpath(venv / 'bin/crew')

    entrypoint = run([venv / 'bin/crew', '--version'], cwd=tmp_path)
    assert entrypoint == VERSION

    # Installed code and update metadata must resolve outside the checkout.
    probe = ("import crew, importlib.metadata as m, json, pathlib, sys;"
             "print(json.dumps({'file': str(pathlib.Path(crew.__file__).resolve()),"
             "'prefix': str(pathlib.Path(sys.prefix).resolve()),"
             "'direct': json.loads(m.distribution('crew-runner').read_text('direct_url.json'))}))")
    out = json.loads(run([venv / 'bin/python', '-c', probe], cwd=tmp_path))
    assert Path(out['file']).is_relative_to(Path(out['prefix']))
    assert out['direct']['url'] == source.as_uri()
    assert 'archive_info' not in out['direct'] and 'vcs_info' not in out['direct']
    assert not out['direct'].get('dir_info', {}).get('editable')

    assert marker_lines(home / '.zshenv') == [MARKER]
    assert not (home / '.bash_profile').exists()

    for mode in ('interactive_login', 'interactive', 'login', 'plain'):
        ok, found = shell_finds_crew(home, '/bin/zsh', mode=mode)
        assert ok and found == str(bin_dir / 'crew'), mode


def test_bash_fresh_install_configures_login_and_interactive(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    bin_dir = home / '.local/bin'
    result = call_installer(home, shell='/bin/bash', args=('--yes', '--source', str(source)),
                            extra=offline_pip(offline_wheels))
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker_lines(home / '.bash_profile') == [MARKER]
    assert marker_lines(home / '.bashrc') == [MARKER]
    assert not (home / '.zshenv').exists()
    assert shell_finds_crew(home, '/bin/bash', mode='login')[0]
    assert shell_finds_crew(home, '/bin/bash', mode='interactive')[0]


def test_bash_uses_effective_login_file(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    # bash reads the first existing of .bash_profile, .bash_login, .profile.
    (home / '.profile').write_text('# existing user settings\n')
    result = call_installer(home, shell='/bin/bash', args=('--yes', '--source', str(source)),
                            extra=offline_pip(offline_wheels))
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker_lines(home / '.profile') == [MARKER]
    assert '# existing user settings' in (home / '.profile').read_text()
    assert not (home / '.bash_profile').exists()
    assert marker_lines(home / '.bashrc') == [MARKER]
    assert shell_finds_crew(home, '/bin/bash', mode='login')[0]
    assert shell_finds_crew(home, '/bin/bash', mode='interactive')[0]


def test_rerun_is_idempotent_and_retains_installation(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    venv = home / '.local/share/crew/venv'
    args = ('--yes', '--source', str(source))
    first = call_installer(home, args=args, extra=offline_pip(offline_wheels))
    assert first.returncode == 0, first.stdout + first.stderr
    link_before = os.readlink(home / '.local/bin/crew')

    # Second run has no package source at all: a retained install must not need it.
    second = call_installer(home, args=args, extra=offline_without_wheels())
    assert second.returncode == 0, second.stdout + second.stderr
    assert 'retain' in second.stdout.lower()
    assert marker_lines(home / '.zshenv') == [MARKER]
    assert os.readlink(home / '.local/bin/crew') == link_before
    assert run([venv / 'bin/crew', '--version'], cwd=tmp_path) == VERSION


def test_same_version_from_other_source_refused(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source_a = make_checkout(tmp_path, 'source-a')
    source_b = make_checkout(tmp_path, 'source-b')
    first = call_installer(home, args=('--yes', '--source', str(source_a)),
                           extra=offline_pip(offline_wheels))
    assert first.returncode == 0, first.stdout + first.stderr
    venv = home / '.local/share/crew/venv'
    direct = installed_metadata(venv) / 'direct_url.json'
    sentinel = venv / 'owned-marker'
    sentinel.write_text('keep\n')
    link = home / '.local/bin/crew'
    link_before = os.readlink(link)
    assert json.loads(direct.read_text())['url'] == source_a.as_uri()

    second = call_installer(home, args=('--yes', '--source', str(source_b)),
                            extra=offline_pip(offline_wheels))
    assert second.returncode != 0
    assert 'error:' in second.stderr
    assert json.loads(direct.read_text())['url'] == source_a.as_uri()
    assert sentinel.read_text() == 'keep\n'
    assert os.readlink(link) == link_before


def test_editable_metadata_refused(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    first = call_installer(home, args=('--yes', '--source', str(source)),
                           extra=offline_pip(offline_wheels))
    assert first.returncode == 0, first.stdout + first.stderr
    venv = home / '.local/share/crew/venv'
    direct = installed_metadata(venv) / 'direct_url.json'
    direct.write_text(json.dumps({'url': source.as_uri(), 'dir_info': {'editable': True}}))

    second = call_installer(home, args=('--yes', '--source', str(source)),
                            extra=offline_pip(offline_wheels))
    assert second.returncode != 0
    assert 'error:' in second.stderr
    assert json.loads(direct.read_text())['dir_info']['editable'] is True


def test_foreign_empty_environment_refused_and_preserved(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    venv = home / '.local/share/crew/venv'
    venv.parent.mkdir(parents=True)
    run([sys.executable, '-m', 'venv', venv], cwd=tmp_path)
    sentinel = venv / 'unrelated-user-file'
    sentinel.write_text('preserve me\n')
    result = call_installer(home, args=('--yes', '--source', str(source)),
                            extra=offline_pip(offline_wheels))
    assert result.returncode != 0
    assert 'error:' in result.stderr
    assert sentinel.read_text() == 'preserve me\n'
    assert not (venv / 'bin/crew').exists()
    assert not (home / '.local/bin/crew').exists()
    assert not (home / '.zshenv').exists()


def test_zshrc_path_overwrite_is_handled(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    bin_dir = home / '.local/bin'
    original = 'export PATH="/usr/bin:/bin"\n'
    zshrc = home / '.zshrc'
    zshrc.write_text(original)
    result = call_installer(home, shell='/bin/zsh', args=('--yes', '--source', str(source)),
                            extra=offline_pip(offline_wheels))
    assert result.returncode == 0, result.stdout + result.stderr
    assert original in zshrc.read_text()
    assert marker_lines(zshrc) == [MARKER]
    for mode in ('interactive_login', 'interactive', 'login', 'plain'):
        ok, found = shell_finds_crew(home, '/bin/zsh', mode=mode)
        assert ok and found == str(bin_dir / 'crew'), mode


def test_unresolvable_zsh_login_override_refused(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    (home / '.zprofile').write_text('export PATH="/usr/bin:/bin"\n')
    result = call_installer(home, shell='/bin/zsh', args=('--yes', '--source', str(source)),
                            extra=offline_pip(offline_wheels))
    assert result.returncode != 0
    assert 'error:' in result.stderr


def test_existing_path_line_is_not_duplicated(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    bin_dir = home / '.local/bin'
    (home / '.zshenv').write_text(f'{MARKER}\nexport PATH="{bin_dir}:$PATH"\n')
    result = call_installer(home, args=('--yes', '--source', str(source)),
                            extra=offline_pip(offline_wheels))
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker_lines(home / '.zshenv') == [MARKER]
    assert (home / '.zshenv').read_text().count(str(bin_dir)) == 1


def test_comment_only_marker_is_repaired(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    (home / '.zshenv').write_text(MARKER + '\n')
    result = call_installer(home, args=('--yes', '--source', str(source)),
                            extra=offline_pip(offline_wheels))
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker_lines(home / '.zshenv') == [MARKER]
    assert 'export PATH=' in (home / '.zshenv').read_text()
    assert shell_finds_crew(home, '/bin/zsh', mode='login')[0]


def test_stale_marker_directory_is_repaired(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    bin_dir = home / '.local/bin'
    (home / '.zshenv').write_text(MARKER + '\nexport PATH="/some/other/bin:$PATH"\n')
    result = call_installer(home, args=('--yes', '--source', str(source)),
                            extra=offline_pip(offline_wheels))
    assert result.returncode == 0, result.stdout + result.stderr
    text = (home / '.zshenv').read_text()
    assert marker_lines(home / '.zshenv') == [MARKER]
    assert '/some/other/bin' not in text
    assert str(bin_dir) in text
    assert shell_finds_crew(home, '/bin/zsh', mode='login')[0]


def test_existing_fresh_shell_path_is_not_changed(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    original = 'export PATH="$HOME/.local/bin:$PATH"\n'
    (home / '.zshenv').write_text(original)
    result = call_installer(home, args=('--yes', '--source', str(source)),
                            extra=offline_pip(offline_wheels))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'fresh' in result.stdout.lower()
    assert (home / '.zshenv').read_text() == original
    assert shell_finds_crew(home, '/bin/zsh', mode='login')[0]


def test_foreign_crew_command_is_not_treated_as_configured(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    bin_dir = home / '.local/bin'
    foreign = home / 'foreign'
    foreign.mkdir(parents=True)
    stub = foreign / 'crew'
    stub.write_text('#!/bin/sh\necho foreign\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    # A fresh shell finds a different crew command, not this installation's link.
    (home / '.zshenv').write_text('export PATH="$HOME/foreign:$PATH"\n')
    result = call_installer(home, args=('--yes', '--source', str(source)),
                            extra=offline_pip(offline_wheels))
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker_lines(home / '.zshenv') == [MARKER]
    ok, found = shell_finds_crew(home, '/bin/zsh', mode='login')
    assert ok and found == str(bin_dir / 'crew')


def test_zdotdir_is_respected(tmp_path, offline_wheels):
    home = tmp_path / 'home'
    home.mkdir()
    zdotdir = home / 'zsh config'
    zdotdir.mkdir()
    source = make_checkout(tmp_path)
    bin_dir = home / '.local/bin'
    result = call_installer(home, shell='/bin/zsh', args=('--yes', '--source', str(source)),
                            extra={**offline_pip(offline_wheels), 'ZDOTDIR': str(zdotdir)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker_lines(zdotdir / '.zshenv') == [MARKER]
    assert not (home / '.zshenv').exists()
    assert shell_finds_crew(home, '/bin/zsh', mode='plain', zdotdir=zdotdir)[0]


def test_paths_with_spaces_and_quotes_are_safe(tmp_path, offline_wheels):
    canary = tmp_path / 'PWNED'
    home = tmp_path / 'ho me'
    home.mkdir()
    # The source path holds an injection attempt: quoting must pass it to pip as
    # data and never evaluate it as a command.
    weird = tmp_path / ('we ird $(touch ' + str(canary) + ") 'q'")
    source = make_checkout(weird, 'src dir')
    bin_dir = tmp_path / "bi n 'q'"
    result = call_installer(home, shell='/bin/zsh', args=(
        '--yes', '--source', str(source), '--bin-dir', str(bin_dir)),
        extra=offline_pip(offline_wheels))
    assert result.returncode == 0, result.stdout + result.stderr
    assert not canary.exists()
    link = bin_dir / 'crew'
    assert link.is_symlink()
    assert os.path.realpath(link) == os.path.realpath(home / '.local/share/crew/venv/bin/crew')
    assert marker_lines(home / '.zshenv') == [MARKER]
    ok, found = shell_finds_crew(home, '/bin/zsh', mode='login')
    assert ok and found == str(bin_dir / 'crew')


@pytest.mark.parametrize('name', ['bi n $HOME', 'bi n `id`', 'bi n "q"', 'bi n \\q'])
def test_unsafe_command_link_path_refused_before_writes(tmp_path, name):
    home = tmp_path / 'home'
    home.mkdir()
    source = make_checkout(tmp_path)
    bin_dir = tmp_path / name
    result = call_installer(home, args=(
        '--yes', '--source', str(source), '--bin-dir', str(bin_dir)))
    assert result.returncode != 0
    assert 'error:' in result.stderr
    assert not bin_dir.exists()
    assert not (home / '.local/share/crew/venv').exists()
    assert not (home / '.zshenv').exists()
