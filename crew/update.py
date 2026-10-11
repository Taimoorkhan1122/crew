"""Explicit release updates for a regular local-source installation."""
from __future__ import annotations

import ast
from contextlib import contextmanager
import fcntl
import shutil
import shlex
import signal
import tempfile
from dataclasses import dataclass
import hashlib
import os
import stat
import subprocess
import tomllib
from datetime import datetime
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = 'https://github.com/Taimoorkhan1122/crew.git'
API = 'https://api.github.com/repos/Taimoorkhan1122/crew'
WEB = 'https://github.com/Taimoorkhan1122/crew'
STABLE = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)')
SHA = re.compile(r'[0-9a-f]{40}')


class UpdateError(ValueError):
    pass


class NoRelease(UpdateError):
    pass


@dataclass(frozen=True)
class Release:
    tag: str
    version: str
    commit: str


@dataclass(frozen=True)
class Targets:
    source: Path
    environment: Path
    version: str


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def read_json(url):
    request = Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'crew-updater'})
    try:
        with build_opener(NoRedirect).open(request, timeout=10) as response:
            content = response.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise UpdateError('release response is too large')
        return json.loads(content)
    except HTTPError as error:
        if error.code == 404 and url == API + '/releases/latest':
            raise NoRelease('no published stable release is available; no installation changes') from None
        raise UpdateError(f'release lookup failed: HTTP {error.code}; no installation changes') from None
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise UpdateError(f'release lookup failed: {error}; no installation changes') from None


def latest_release():
    result = read_json(API + '/releases/latest')
    if not isinstance(result, dict):
        raise UpdateError('invalid release metadata')
    tag = result.get('tag_name')
    version = tag.removeprefix('v') if isinstance(tag, str) else ''
    if (result.get('draft') is not False or result.get('prerelease') is not False
            or not STABLE.fullmatch(version) or type(result.get('id')) is not int or result['id'] <= 0
            or result.get('url') != API + '/releases/' + str(result['id'])
            or result.get('html_url') != WEB + '/releases/tag/' + str(tag)
            or result.get('tarball_url') != API + '/tarball/' + str(tag)):
        raise UpdateError('invalid or unsafe stable release metadata')
    try:
        datetime.fromisoformat(result['published_at'].replace('Z', '+00:00'))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise UpdateError('release publication time is missing or invalid') from None
    reference = read_json(API + '/git/ref/tags/' + tag)
    if not isinstance(reference, dict) or reference.get('ref') != 'refs/tags/' + tag:
        raise UpdateError('release tag identity is invalid')
    obj = reference.get('object')
    for _ in range(5):
        if not isinstance(obj, dict) or not isinstance(obj.get('sha'), str) or not SHA.fullmatch(obj['sha']):
            raise UpdateError('release commit identity is invalid')
        if obj.get('type') == 'commit':
            return Release(tag, version, obj['sha'])
        if obj.get('type') != 'tag':
            raise UpdateError('release tag does not identify a commit')
        tagged = read_json(API + '/git/tags/' + obj['sha'])
        obj = tagged.get('object') if isinstance(tagged, dict) else None
    raise UpdateError('release tag nesting is unsupported')


def discover():
    try:
        distribution = metadata.distribution('crew-runner')
        direct = json.loads(distribution.read_text('direct_url.json') or '{}')
    except (metadata.PackageNotFoundError, TypeError, ValueError):
        raise UpdateError('installed source metadata is missing; use a regular noneditable local-source installation') from None
    if (not Path(__file__).absolute().is_relative_to(Path(sys.prefix))
            or not Path(distribution.locate_file('crew/cli.py')).absolute().is_relative_to(Path(sys.prefix))):
        raise UpdateError('development source execution cannot self-update; use the installed working CLI')
    if not isinstance(direct, dict) or not isinstance(direct.get('dir_info', {}), dict):
        raise UpdateError('installed source metadata is invalid')
    if direct.get('dir_info', {}).get('editable') is True:
        raise UpdateError('development/editable installations cannot self-update; update the separate working installation')
    if sys.prefix == sys.base_prefix:
        raise UpdateError('self-update requires a dedicated virtual environment')
    url = urlsplit(direct.get('url', ''))
    if url.scheme != 'file' or url.netloc or url.query or url.fragment or 'dir_info' not in direct or 'archive_info' in direct or 'vcs_info' in direct:
        raise UpdateError('self-update requires unambiguous local-directory source metadata')
    return Targets(Path(unquote(url.path)), Path(sys.prefix), distribution.version)



def isolated_env(cache=None):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('GIT_') and key not in (
               'PYTHONPATH', 'PYTHONHOME', 'PIP_TARGET', 'PIP_PREFIX', 'PIP_USER',
               'PIP_ROOT', 'PIP_LOG', 'PIP_REPORT', 'PIP_PYTHON')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_OPTIONAL_LOCKS='0',
               PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_NO_INPUT='1',
               PYTHONDONTWRITEBYTECODE='1')
    if cache is not None:
        env['PIP_CACHE_DIR'] = str(cache)
    return env


def command(args, *, cwd, timeout=60, cache=None):
    try:
        process = subprocess.Popen([str(value) for value in args], cwd=cwd, env=isolated_env(cache),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Build/install descendants must not continue writing after a reported timeout.
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise UpdateError('update command timed out; its process group was stopped') from None
        result = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    except (OSError, subprocess.SubprocessError) as error:
        raise UpdateError(f'update command failed: {error}') from None
    if result.returncode:
        message = (result.stderr or result.stdout).strip()[-2000:]
        raise UpdateError(f'{Path(str(args[0])).name} exited {result.returncode}: {message}')
    return result.stdout.strip()


def git(path, *args):
    return command(['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false',
                    '-C', path, *args], cwd=path.parent)


def safe_directory(path):
    if not path.is_absolute() or path == Path(path.anchor) or not path.is_dir():
        raise UpdateError('update target must be an existing absolute directory')
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise UpdateError('symlink directory targets are unsupported')
    if path != path.resolve():
        raise UpdateError('directory aliases are unsupported')
    return (path.stat().st_dev, path.stat().st_ino)


def source_version(source):
    try:
        project = tomllib.loads((source / 'pyproject.toml').read_text())['project']
        tree = ast.parse((source / 'crew/cli.py').read_text())
        versions = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets)
                    and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
        version = project['version']
        if project['name'] != 'crew-runner' or not isinstance(version, str) or not STABLE.fullmatch(version) or versions != [version]:
            raise ValueError('source package/CLI version mismatch')
        if not (source / 'defaults/config.yaml').is_file() or not (source / 'defaults/flows').is_dir() or not (source / 'defaults/prompts').is_dir():
            raise ValueError('source templates are missing')
        return version
    except (OSError, KeyError, TypeError, ValueError, SyntaxError) as error:
        raise UpdateError(f'invalid packaged source: {error}') from None


def snapshot(path, *, allow_symlinks=False):
    records = {}
    for item in sorted(path.rglob('*')):
        relative = str(item.relative_to(path))
        mode = stat.S_IMODE(item.lstat().st_mode)
        if item.is_symlink():
            if not allow_symlinks:
                raise UpdateError('source symlink content is unsupported: ' + relative)
            records[relative] = ('link', os.readlink(item), mode)
        elif item.is_file():
            records[relative] = ('file', hashlib.sha256(item.read_bytes()).hexdigest(), mode)
        elif item.is_dir():
            records[relative] = ('directory', mode)
        else:
            raise UpdateError('unsupported filesystem content: ' + relative)
    return records


def preflight(targets):
    source_identity = safe_directory(targets.source)
    environment_identity = safe_directory(targets.environment)
    if targets.source.is_relative_to(targets.environment) or targets.environment.is_relative_to(targets.source):
        raise UpdateError('development/embedded or overlapping source and environment targets are unsupported')
    if not (targets.source / '.git').is_dir() or (targets.source / '.git').is_symlink():
        raise UpdateError('source must be an ordinary Git checkout; linked worktrees are unsupported')
    if git(targets.source, 'rev-parse', '--show-toplevel') != str(targets.source):
        raise UpdateError('source checkout identity is ambiguous')
    origins = git(targets.source, 'remote', 'get-url', '--all', 'origin').splitlines()
    if origins not in ([REPOSITORY], [REPOSITORY.removesuffix('.git')], ['git@github.com:Taimoorkhan1122/crew.git']):
        raise UpdateError('source origin must identify the official Crew repository exactly')
    # Git's local filters can execute arbitrary commands during checkout. Do not use them.
    configuration = git(targets.source, 'config', '--local', '--list')
    if any(line.startswith('filter.') for line in configuration.splitlines()):
        raise UpdateError('custom Git filters are unsupported for self-update')
    status = git(targets.source, 'status', '--porcelain', '--untracked-files=all')
    if status:
        raise UpdateError('source has untracked files or dirty tracked files; preserve them before updating')
    version = source_version(targets.source)
    from crew.cli import VERSION
    if not STABLE.fullmatch(targets.version) or version != targets.version or VERSION != targets.version:
        raise UpdateError('installed distribution, CLI and working source versions do not match')
    return {'head': git(targets.source, 'rev-parse', 'HEAD'), 'source_identity': source_identity,
            'environment_identity': environment_identity, 'source_files': snapshot(targets.source),
            'environment_files': snapshot(targets.environment, allow_symlinks=True)}


@contextmanager
def update_lock(targets):
    store = targets.environment.parent / '.crew-updates'
    if store.is_relative_to(targets.source) or store.is_relative_to(targets.environment):
        raise UpdateError('recovery storage overlaps an update target')
    store.mkdir(mode=0o700, exist_ok=True)
    safe_directory(store)
    descriptor = os.open(store / 'update.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise UpdateError('another update holds the installation lock') from None
        yield store
    finally:
        os.close(descriptor)


def download(release, destination):
    command(['git', '-c', 'core.hooksPath=' + os.devnull, 'clone', '--no-checkout',
             REPOSITORY, destination], cwd=destination.parent, timeout=120)


def verify_install(environment, version, work, source=None):
    python = environment / 'bin/python'
    script = """import importlib,importlib.metadata as m,json,pathlib,pkgutil,sys,crew
expected=sys.argv[1]
assert m.version('crew-runner') == expected
assert pathlib.Path(crew.__file__).is_relative_to(pathlib.Path(sys.prefix))
for module in pkgutil.walk_packages(crew.__path__, crew.__name__+'.'):
    importlib.import_module(module.name)
from crew.cli import VERSION
assert VERSION == expected
root=pathlib.Path(sys.prefix)/'share/crew'
assert (root/'config.yaml').is_file() and list((root/'flows').glob('*.yaml')) and list((root/'prompts').rglob('*.md'))
if len(sys.argv)>2:
    assert json.loads(m.distribution('crew-runner').read_text('direct_url.json')) == {'dir_info':{},'url':pathlib.Path(sys.argv[2]).as_uri()}
"""
    command([python, '-I', '-B', '-c', script, version, *([source] if source else [])], cwd=work)
    if command([environment / 'bin/crew', '--version'], cwd=work) != version:
        raise UpdateError('installed entrypoint version verification failed')
    command([environment / 'bin/crew', '--help'], cwd=work)


def prepare(targets, release, work):
    downloaded = work / 'downloaded'
    download(release, downloaded)
    if git(downloaded, 'rev-parse', 'refs/tags/' + release.tag + '^{commit}') != release.commit:
        raise UpdateError('release tag moved after lookup; no installation changes')
    # Fetch and apply only inside the complete source copy. Ignored user files stay present.
    prepared = work / 'prepared_source'
    shutil.copytree(targets.source, prepared)
    git(prepared, 'fetch', '--no-tags', str(downloaded), release.commit)
    git(prepared, 'merge-base', '--is-ancestor', 'HEAD', release.commit)
    ignored = git(prepared, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z').split('\0')
    tracked = git(prepared, 'ls-tree', '-r', '--name-only', '-z', release.commit).split('\0')
    for local in filter(None, ignored):
        for incoming in filter(None, tracked):
            if local == incoming or local.startswith(incoming + '/') or incoming.startswith(local + '/'):
                raise UpdateError('release collides with preserved ignored file: ' + local)
    git(prepared, 'merge', '--ff-only', release.commit)
    snapshot(prepared)  # Refuse symlinks and special content before running package build code.
    if source_version(prepared) != release.version:
        raise UpdateError('release source version does not match the published version')
    trial = work / 'trial'
    command([targets.environment / 'bin/python', '-I', '-B', '-m', 'venv', trial], cwd=work, timeout=120)
    python = trial / 'bin/python'
    command([python, '-I', '-B', '-m', 'pip', 'install', '--no-compile', 'setuptools>=68'], cwd=work, timeout=120, cache=work / 'cache')
    command([python, '-I', '-B', '-m', 'pip', 'wheel', '--no-build-isolation', '--no-deps',
             '--wheel-dir', work / 'wheels', prepared], cwd=work, timeout=120, cache=work / 'cache')
    wheels = list((work / 'wheels').glob('crew_runner-*.whl'))
    if len(wheels) != 1:
        raise UpdateError('release wheel identity is ambiguous')
    command([python, '-I', '-B', '-m', 'pip', 'install', '--no-compile', wheels[0]], cwd=work, timeout=120, cache=work / 'cache')
    verify_install(trial, release.version, work)
    return prepared


def install(targets, work):
    # Install the final source path so future discovery never points at a temporary wheel.
    command([targets.environment / 'bin/python', '-I', '-B', '-m', 'pip', 'install',
             '--force-reinstall', '--no-compile', targets.source], cwd=work, timeout=180, cache=work / 'cache')


def recovery_commands(targets, work):
    quote = lambda path: shlex.quote(str(path))
    return [
        f'test ! -e {quote(work / "failed_source")} && if test -e {quote(targets.source)}; then mv {quote(targets.source)} {quote(work / "failed_source")}; fi',
        f'test ! -e {quote(targets.source)} && cp -a {quote(work / "old_source")} {quote(targets.source)}',
        f'test ! -e {quote(work / "failed_environment")} && mv {quote(targets.environment)} {quote(work / "failed_environment")}',
        f'test ! -e {quote(targets.environment)} && cp -a {quote(work / "old_environment")} {quote(targets.environment)}',
    ]


def apply_update(targets, release, current, details):
    mutated = False
    work = None
    try:
        with update_lock(targets) as store:
            work = Path(tempfile.mkdtemp(prefix='update-', dir=store))
            work_identity = safe_directory(work)
            details['recovery'] = str(work)
            prepared = prepare(targets, release, work)
            shutil.copytree(targets.environment, work / 'old_environment', symlinks=True)
            if snapshot(work / 'old_environment', allow_symlinks=True) != current['environment_files']:
                raise UpdateError('environment changed during backup; no installation changes')
            if discover() != targets or preflight(targets) != current or safe_directory(work) != work_identity:
                raise UpdateError('installation changed during preparation; no installation changes')
            commands = recovery_commands(targets, work)
            (work / 'RECOVERY.json').write_text(json.dumps({'source': str(targets.source),
                'environment': str(targets.environment), 'commands': commands}, indent=2) + '\n')
            mutated = True
            targets.source.rename(work / 'old_source')
            if snapshot(work / 'old_source') != current['source_files']:
                raise UpdateError('source changed during replacement; preserved source needs recovery')
            prepared.rename(targets.source)
            install(targets, work)
            verify_install(targets.environment, release.version, work, targets.source)
            return {**details, 'status': 'updated', 'previous_version': targets.version,
                    'installed_version': release.version}, 0
    except (UpdateError, OSError, ValueError, TypeError) as error:
        result = {**details, 'status': 'recovery_required' if mutated else 'refused', 'error': str(error)}
        if mutated:
            result['recovery_commands'] = recovery_commands(targets, work)
        return result, 1


def run_update(*, check=False):
    details = {'check': check}
    try:
        targets = discover()
        details.update(installed_version=targets.version, source=str(targets.source), environment=str(targets.environment))
        current = preflight(targets)
        release = latest_release()
        details.update(release_version=release.version, release_commit=release.commit)
        installed_number = tuple(map(int, targets.version.split('.')))
        release_number = tuple(map(int, release.version.split('.')))
        if release_number < installed_number:
            raise UpdateError('refusing a release downgrade; no installation changes')
        if release_number == installed_number:
            if release.commit != current['head']:
                raise UpdateError('same-version release commit differs from working source; no installation changes')
            return {**details, 'status': 'up_to_date'}, 0
        if check:
            return {**details, 'status': 'available'}, 0
        return apply_update(targets, release, current, details)
    except NoRelease as error:
        return {**details, 'status': 'no_release', 'error': str(error)}, 1
    except (UpdateError, OSError, ValueError, TypeError) as error:
        return {**details, 'status': 'refused', 'error': str(error)}, 1
