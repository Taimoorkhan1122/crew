"""Disposable fixtures for guided-installer subprocess tests.

These helpers build throwaway Git checkouts, temporary user homes, and clean
environments. They never touch the real home directory or the real Crew
installation.
"""
import os
from pathlib import Path
import shutil
import subprocess

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / 'scripts' / 'install.sh'
OFFICIAL_ORIGIN = 'https://github.com/Taimoorkhan1122/crew.git'
BASELINE_PATH = '/usr/bin:/bin:/usr/sbin:/sbin'
MARKER = '# Crew command-line tool PATH'


def run(args, *, cwd, env=None):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def make_checkout(base, name='source'):
    """Create a disposable clean Crew Git checkout with the official origin."""
    base = Path(base)
    source = base / name
    source.mkdir(parents=True)
    shutil.copytree(REPO / 'crew', source / 'crew', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(REPO / 'defaults', source / 'defaults')
    shutil.copy2(REPO / 'pyproject.toml', source / 'pyproject.toml')
    (source / '.gitignore').write_text('build/\n*.egg-info/\n__pycache__/\n.venv/\n.DS_Store\n.crew/\n.env\n')
    run(['git', 'init', '-b', 'main', source], cwd=base)
    run(['git', '-C', source, 'config', 'user.name', 'Installer fixture'], cwd=base)
    run(['git', '-C', source, 'config', 'user.email', 'fixture@example.invalid'], cwd=base)
    run(['git', '-C', source, 'add', '.'], cwd=base)
    run(['git', '-C', source, 'commit', '-m', 'installer fixture'], cwd=base)
    run(['git', '-C', source, 'remote', 'add', 'origin', OFFICIAL_ORIGIN], cwd=base)
    return source


def install_env(home, shell='/bin/zsh', *, path=None, extra=None):
    """Clean environment for the installer. Includes python3/git, not the target bin."""
    env = {
        'HOME': str(home),
        'SHELL': shell,
        'PATH': path if path is not None else os.environ['PATH'],
        'TERM': 'dumb',
        'LANG': os.environ.get('LANG', 'en_US.UTF-8'),
        'TMPDIR': os.environ.get('TMPDIR', '/tmp'),
    }
    if extra:
        env.update(extra)
    return env


def offline_pip(wheelhouse):
    """Environment that resolves the build backend and dependencies offline."""
    return {
        'PIP_NO_INDEX': '1',
        'PIP_FIND_LINKS': str(wheelhouse),
        'PIP_CONFIG_FILE': os.devnull,
        'PIP_DISABLE_PIP_VERSION_CHECK': '1',
        'PIP_NO_INPUT': '1',
    }


def offline_without_wheels():
    """Environment with no package source. Any real build or download must fail."""
    return {
        'PIP_NO_INDEX': '1',
        'PIP_CONFIG_FILE': os.devnull,
        'PIP_DISABLE_PIP_VERSION_CHECK': '1',
        'PIP_NO_INPUT': '1',
    }


def call_installer(home, *, shell='/bin/zsh', args=(), path=None, extra=None,
                   stdin=None, cwd=None):
    env = install_env(home, shell, path=path, extra=extra)
    return subprocess.run(['bash', str(SCRIPT), *args], env=env, cwd=cwd,
                          input=stdin, capture_output=True, text=True,
                          timeout=600)


def shell_finds_crew(home, shell, *, mode='login', zdotdir=None):
    """Independent fresh-shell probe with a sanitized PATH.

    Uses a baseline PATH that excludes the installer's bin directory, so a
    success proves the startup files configured PATH, not an activated state.
    """
    command = [shell]
    flags = {
        'login': ['-l'],
        'interactive': ['-i'],
        'interactive_login': ['-l', '-i'],
        'plain': [],
    }[mode]
    command += flags
    command += ['-c', 'command -v crew']
    env = {'HOME': str(home), 'PATH': BASELINE_PATH, 'TERM': 'dumb'}
    if zdotdir is not None:
        env['ZDOTDIR'] = str(zdotdir)
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    return result.returncode == 0, result.stdout.strip()
