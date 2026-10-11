"""Disposable real install/Git fixtures for updater verification."""
from importlib import metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile


def run(args, *, cwd, env=None):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def wheelhouse(directory, backend):
    directory.mkdir()
    packages = ['psutil', 'pydantic', 'pydantic_core', 'PyYAML', 'annotated-types', 'typing-extensions', 'typing-inspection']
    for name in packages:
        distribution = metadata.distribution(name)
        info = next(Path(item).parts[0] for item in distribution.files if str(item).endswith('.dist-info/WHEEL'))
        tag = next(line[5:] for line in distribution.read_text('WHEEL').splitlines() if line.startswith('Tag: '))
        filename = distribution.metadata['Name'].replace('-', '_') + '-' + distribution.version + '-' + tag + '.whl'
        with zipfile.ZipFile(directory / filename, 'w', zipfile.ZIP_DEFLATED) as wheel:
            for item in distribution.files:
                if '..' not in Path(item).parts and '__pycache__' not in Path(item).parts and not str(item).endswith('.pyc'):
                    wheel.write(distribution.locate_file(item), str(item))
    backend = Path(backend)
    info = next(backend.glob('setuptools-*.dist-info'))
    version = info.name.removeprefix('setuptools-').removesuffix('.dist-info')
    with zipfile.ZipFile(directory / f'setuptools-{version}-py3-none-any.whl', 'w', zipfile.ZIP_DEFLATED) as wheel:
        for item in backend.rglob('*'):
            if item.is_file() and '__pycache__' not in item.parts and item.suffix != '.pyc':
                wheel.write(item, item.relative_to(backend))
    return directory


def make_install(base, wheels):
    root = Path(__file__).resolve().parents[1]
    repository = base / 'publisher'
    repository.mkdir()
    shutil.copytree(root / 'crew', repository / 'crew', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(root / 'defaults', repository / 'defaults')
    shutil.copy2(root / 'pyproject.toml', repository / 'pyproject.toml')
    (repository / '.gitignore').write_text('build/\n*.egg-info/\n.DS_Store\n.crew/\n.env\n')
    run(['git', 'init', '-b', 'main', repository], cwd=base)
    run(['git', '-C', repository, 'config', 'user.name', 'Updater fixture'], cwd=base)
    run(['git', '-C', repository, 'config', 'user.email', 'fixture@example.invalid'], cwd=base)
    run(['git', '-C', repository, 'add', '.'], cwd=base)
    run(['git', '-C', repository, 'commit', '-m', 'old stable package'], cwd=base)
    old = run(['git', '-C', repository, 'rev-parse', 'HEAD'], cwd=base)
    run(['git', '-C', repository, 'tag', 'v0.1.0'], cwd=base)
    source = base / 'working source'
    run(['git', 'clone', repository, source], cwd=base)
    run(['git', '-C', source, 'remote', 'set-url', 'origin', 'https://github.com/Taimoorkhan1122/crew.git'], cwd=base)
    environment = base / 'installed environment'
    run([sys.executable, '-I', '-m', 'venv', environment], cwd=base)
    env = {**os.environ, 'PIP_NO_INDEX': '1', 'PIP_FIND_LINKS': str(wheels), 'PIP_CONFIG_FILE': os.devnull,
           'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    run([environment / 'bin/python', '-I', '-m', 'pip', 'install', 'setuptools>=68'], cwd=base, env=env)
    run([environment / 'bin/python', '-I', '-m', 'pip', 'install', '--no-build-isolation', source], cwd=base, env=env)
    for path in (repository / 'pyproject.toml', repository / 'crew/cli.py'):
        path.write_text(path.read_text().replace('0.1.0', '0.1.1'))
    (repository / 'defaults/config.yaml').write_text((repository / 'defaults/config.yaml').read_text() + '\n# release 0.1.1\n')
    run(['git', '-C', repository, 'add', '.'], cwd=base)
    run(['git', '-C', repository, 'commit', '-m', 'new stable release'], cwd=base)
    commit = run(['git', '-C', repository, 'rev-parse', 'HEAD'], cwd=base)
    run(['git', '-C', repository, 'tag', 'v0.1.1'], cwd=base)
    return {'source': source, 'environment': environment, 'publisher': repository, 'old': old, 'commit': commit, 'env': env}


def installed_call(fixture, code, *, argv=(), cwd=None):
    """Run installed code isolated from caller source; only test network boundary is replaced."""
    return subprocess.run([str(fixture['environment'] / 'bin/python'), '-I', '-B', '-c', code, *argv],
                          cwd=cwd or fixture['source'].parent, env=fixture['env'], capture_output=True, text=True, timeout=180)
