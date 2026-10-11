#!/usr/bin/env bash
# Guided user-level installer for the Crew command-line tool.
#
# The installer builds Crew from a local Git checkout into a separate virtual
# environment, links the command into a user bin directory, and configures the
# login shell PATH when the shell does not already find the command. It changes
# no system files and needs no sudo.
set -u

REPOSITORY="https://github.com/Taimoorkhan1122/crew.git"
MARKER="# Crew command-line tool PATH"
PROBE_PATH="/usr/bin:/bin:/usr/sbin:/sbin"

usage() {
  cat <<'EOF'
Usage: bash scripts/install.sh [options]

Install the Crew command-line tool for the current user from a local Git checkout.

Options:
  --yes           Do not ask for confirmation.
  --source DIR    Source Git checkout. Default: the repository of this script.
  --venv DIR      Virtual environment. Default: $HOME/.local/share/crew/venv
  --bin-dir DIR   Command link directory. Default: $HOME/.local/bin
  -h, --help      Show this help and exit.

Supported shells are zsh and bash. The installer adds a PATH entry to the
startup files of the shell selected by SHELL. It makes no system-wide change
and uses no sudo. Run the command again to keep a compatible installation.
Use 'crew update' to install a new release.

The environment and command link paths can contain spaces. They must not
contain the characters $, `, ", or \ because macOS login shells re-evaluate
the PATH value.
EOF
}

info() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
usage_error() { usage >&2; exit 2; }

# Quote a value for a double-quoted shell context. Newlines are refused earlier.
shell_double_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//\$/\\\$}"
  value="${value//\`/\\\`}"
  printf '%s' "$value"
}

# Refuse path characters that macOS login shells cannot keep in PATH. The
# system path_helper re-evaluates PATH, so these characters cannot stay literal.
path_has_unsafe_characters() {
  case "$1" in
    *$'\n'*|*'"'*|*\\*|*'$'*|*'`'*) return 0 ;;
    *) return 1 ;;
  esac
}

# True when an existing component of a path is a symlink. This stops a write
# from leaving the requested directory tree.
path_has_symlink_component() {
  local rest component current=""
  case "$1" in
    /*) rest="${1#/}" ;;
    *) rest="$PWD/${1#./}" ;;
  esac
  while [ -n "$rest" ]; do
    case "$rest" in
      */*) component="${rest%%/*}"; rest="${rest#*/}" ;;
      *) component="$rest"; rest="" ;;
    esac
    [ -z "$component" ] && continue
    current="$current/$component"
    if [ -L "$current" ]; then return 0; fi
    if [ ! -e "$current" ]; then break; fi
  done
  return 1
}

# Resolve a path to an absolute canonical path, including a path that does not exist.
canonical_path() {
  local target="$1" suffix="" probe base
  case "$target" in
    /*) ;;
    *) target="$PWD/$target" ;;
  esac
  probe="$target"
  while [ ! -e "$probe" ] && [ "$probe" != "/" ]; do
    if [ -z "$suffix" ]; then suffix="$(basename "$probe")"; else suffix="$(basename "$probe")/$suffix"; fi
    probe="$(dirname "$probe")"
  done
  base="$(cd -P "$probe" 2>/dev/null && pwd -P)" || base="$probe"
  if [ -n "$suffix" ]; then printf '%s/%s\n' "$base" "$suffix"; else printf '%s\n' "$base"; fi
}

read_source_version() {
  "$python_bin" - "$source_dir" <<'PY'
import ast
import pathlib
import sys
import tomllib
source = pathlib.Path(sys.argv[1])
try:
    project = tomllib.loads((source / 'pyproject.toml').read_text())['project']
    tree = ast.parse((source / 'crew/cli.py').read_text())
    versions = [node.value.value for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
    version = project['version']
    if project['name'] != 'crew-runner' or versions != [version]:
        raise ValueError('the package name or the package and CLI versions do not agree')
    print(version)
except Exception as error:  # noqa: BLE001 - report any read or parse failure
    raise SystemExit(f'cannot read project version: {error}')
PY
}

# True when the existing environment is a genuine, noneditable, local-directory
# installation of exactly the requested source and version. It writes nothing.
environment_owned() {
  [ -d "$venv_dir" ] || return 1
  [ -x "$venv_dir/bin/python" ] || return 1
  [ -x "$venv_dir/bin/crew" ] || return 1
  "$venv_dir/bin/python" -I - "$venv_dir" "$source_dir" "$source_version" <<'PY'
import importlib.metadata as metadata
import json
import pathlib
import sys
import urllib.parse
venv = pathlib.Path(sys.argv[1]).resolve()
source = pathlib.Path(sys.argv[2]).resolve()
version = sys.argv[3]
def fail(message):
    sys.stderr.write(message + '\n')
    raise SystemExit(1)
if pathlib.Path(sys.prefix).resolve() != venv:
    fail('the environment prefix does not match the requested environment')
try:
    distribution = metadata.distribution('crew-runner')
except metadata.PackageNotFoundError:
    fail('the environment has no crew-runner distribution')
if distribution.version != version:
    fail('the installed version does not match the source version')
if not pathlib.Path(distribution.locate_file('')).resolve().is_relative_to(venv):
    fail('the installed metadata is outside the environment')
try:
    import crew
except Exception:
    fail('the installed package cannot be imported')
if not pathlib.Path(crew.__file__).resolve().is_relative_to(venv):
    fail('the installed package is outside the environment')
try:
    direct = json.loads(distribution.read_text('direct_url.json') or '{}')
except Exception:
    fail('the installation metadata is missing or invalid')
if (not isinstance(direct, dict) or 'dir_info' not in direct
        or 'archive_info' in direct or 'vcs_info' in direct):
    fail('the installation is not a local directory install')
if direct.get('dir_info', {}).get('editable'):
    fail('an editable installation is not supported')
url = direct.get('url')
if not isinstance(url, str):
    fail('the installation metadata has no source URL')
parts = urllib.parse.urlsplit(url)
if parts.scheme != 'file' or parts.netloc or parts.query or parts.fragment:
    fail('the installation source is not a local directory')
try:
    if pathlib.Path(urllib.parse.unquote(parts.path)).resolve() != source:
        fail('the installation comes from a different source')
except OSError:
    fail('the installation source cannot be resolved')
PY
}

# Remove an existing installer PATH block (its marker and following export line).
remove_marker_block() {
  local rc="$1" tmp
  [ -f "$rc" ] || return 0
  tmp="$(mktemp "$(dirname "$rc")/.crew-rc.XXXXXX" 2>/dev/null)" || return 1
  awk -v marker="$MARKER" '
    $0 == marker { skip = 1; next }
    skip == 1 && $0 ~ /^export PATH=/ { skip = 0; next }
    { skip = 0; print }
  ' "$rc" > "$tmp" || { rm -f "$tmp"; return 1; }
  cat "$tmp" > "$rc" || { rm -f "$tmp"; return 1; }
  rm -f "$tmp"
  return 0
}

assume_yes=0
source_dir=""
venv_dir=""
bin_dir=""
while [ $# -gt 0 ]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --yes|-y) assume_yes=1 ;;
    --source)
      [ $# -ge 2 ] || usage_error
      source_dir="$2"; shift ;;
    --venv)
      [ $# -ge 2 ] || usage_error
      venv_dir="$2"; shift ;;
    --bin-dir)
      [ $# -ge 2 ] || usage_error
      bin_dir="$2"; shift ;;
    --) shift; [ $# -eq 0 ] || usage_error; break ;;
    *) usage_error ;;
  esac
  shift
done
[ $# -eq 0 ] || usage_error

[ -n "${HOME:-}" ] || die "HOME is not set; a user home directory is required"
if path_has_unsafe_characters "$HOME"; then
  die "HOME contains characters that macOS login shells cannot keep in PATH (\\$ \" backtick backslash): $HOME"
fi
script_dir="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
default_source="$(cd -P "$script_dir/.." && pwd)"
source_dir="${source_dir:-$default_source}"
venv_dir="${venv_dir:-$HOME/.local/share/crew/venv}"
bin_dir="${bin_dir:-$HOME/.local/bin}"
link_path="$bin_dir/crew"

# --- Preflight: shell support -------------------------------------------------
shell_path="${SHELL:-}"
[ -n "$shell_path" ] || die "SHELL is not set; supported shells are zsh and bash"
shell_name="$(basename "$shell_path")"
case "$shell_name" in
  zsh|bash) ;;
  *) die "unsupported shell '$shell_name'; supported shells are zsh and bash" ;;
esac

# Probe a fresh shell with a sanitized PATH. Success requires the shell to find
# exactly the expected command. The current process PATH and other crew
# commands do not count.
probe_shell_expected_command() {
  local flag="$1" output
  if [ -n "${ZDOTDIR:-}" ]; then
    output="$(env -i HOME="$HOME" ZDOTDIR="$ZDOTDIR" PATH="$PROBE_PATH" "$shell_path" $flag -c 'command -v crew' 2>/dev/null)" || return 1
  else
    output="$(env -i HOME="$HOME" PATH="$PROBE_PATH" "$shell_path" $flag -c 'command -v crew' 2>/dev/null)" || return 1
  fi
  [ "$output" = "$link_path" ] || return 1
  return 0
}

fresh_shell_finds_crew() {
  case "$shell_name" in
    zsh) probe_shell_expected_command "-l -i" && probe_shell_expected_command "-i" \
           && probe_shell_expected_command "-l" && probe_shell_expected_command "" ;;
    bash) probe_shell_expected_command "-l" && probe_shell_expected_command "-i" ;;
    *) return 1 ;;
  esac
}

# --- Preflight: Python and Git ------------------------------------------------
python_bin="$(command -v python3 || true)"
[ -n "$python_bin" ] || die "Python 3.11 or newer is required; python3 was not found in PATH"
python_report="$("$python_bin" --version 2>/dev/null || true)"
python_number="${python_report#Python }"
case "$python_number" in
  [0-9]*.[0-9]*) ;;
  *) die "Python 3.11 or newer is required; '$python_bin --version' did not report a version" ;;
esac
python_major="${python_number%%.*}"
python_rest="${python_number#*.}"
python_minor="${python_rest%%.*}"
if [ "$python_major" -lt 3 ] || { [ "$python_major" -eq 3 ] && [ "$python_minor" -lt 11 ]; }; then
  die "Python 3.11 or newer is required; found Python $python_number"
fi

git_bin="$(command -v git || true)"
[ -n "$git_bin" ] || die "Git is required; git was not found in PATH"
"$git_bin" --version >/dev/null 2>&1 || die "Git is required; 'git --version' failed"

# --- Preflight: source checkout ----------------------------------------------
[ -d "$source_dir" ] || die "source directory does not exist: $source_dir"
source_dir="$(cd -P "$source_dir" && pwd)" || die "cannot resolve source directory: $source_dir"
if [ ! -d "$source_dir/.git" ] || [ -L "$source_dir/.git" ]; then
  die "source must be an ordinary Git checkout: $source_dir"
fi
top="$("$git_bin" -C "$source_dir" rev-parse --show-toplevel 2>/dev/null || true)"
[ "$top" = "$source_dir" ] || die "source checkout identity is ambiguous: $source_dir"
origins="$("$git_bin" -C "$source_dir" remote get-url --all origin 2>/dev/null || true)"
case "$origins" in
  "$REPOSITORY"|"${REPOSITORY%.git}"|"git@github.com:Taimoorkhan1122/crew.git") ;;
  *) die "source origin must identify the official Crew repository: $REPOSITORY" ;;
esac
[ -f "$source_dir/pyproject.toml" ] || die "source does not look like the Crew project: $source_dir"
source_status="$("$git_bin" -C "$source_dir" status --porcelain --untracked-files=all 2>/dev/null || true)"
[ -z "$source_status" ] || die "source checkout has local changes; commit or remove them before installation: $source_dir"

# --- Preflight: path safety and overlap ---------------------------------------
if path_has_unsafe_characters "$venv_dir"; then
  die "the environment path contains characters that macOS login shells cannot keep in PATH (\\$ \" backtick backslash): $venv_dir"
fi
if path_has_unsafe_characters "$bin_dir"; then
  die "the command link directory contains characters that macOS login shells cannot keep in PATH (\\$ \" backtick backslash): $bin_dir"
fi
canon_source="$source_dir"
canon_venv="$(canonical_path "$venv_dir")"
canon_bin="$(canonical_path "$bin_dir")"
case "$canon_venv/" in "$canon_source/"*) die "the environment must be outside the source checkout (overlap): $venv_dir" ;; esac
case "$canon_source/" in "$canon_venv/"*) die "the source checkout must be outside the environment (overlap): $venv_dir" ;; esac
case "$canon_bin/" in "$canon_source/"*) die "the command link directory must be outside the source checkout: $bin_dir" ;; esac
case "$canon_venv" in "/"|"") die "invalid environment path: $venv_dir" ;; esac

if path_has_symlink_component "$venv_dir"; then
  die "the environment path has a symlinked directory component and is unsupported: $venv_dir"
fi
if path_has_symlink_component "$bin_dir"; then
  die "the command link directory has a symlinked directory component and is unsupported: $bin_dir"
fi
if [ -L "$venv_dir" ]; then die "the environment path is a symlink and is unsupported: $venv_dir"; fi
if [ -e "$venv_dir" ] && [ ! -d "$venv_dir" ]; then die "the environment path exists and is not a directory: $venv_dir"; fi
if [ -e "$venv_dir" ] && [ ! -x "$venv_dir/bin/python" ]; then
  die "the environment directory is not a usable virtual environment: $venv_dir"
fi

if [ -e "$link_path" ] || [ -L "$link_path" ]; then
  if [ ! -L "$link_path" ] || [ "$(readlink "$link_path")" != "$venv_dir/bin/crew" ]; then
    die "the command link already exists and is not this installation's link: $link_path"
  fi
fi

# --- Preflight: shell startup files ------------------------------------------
if [ "$shell_name" = zsh ]; then
  rc_dir="${ZDOTDIR:-$HOME}"
  if [ -n "${ZDOTDIR:-}" ] && [ ! -d "$rc_dir" ]; then
    die "ZDOTDIR does not exist: $rc_dir"
  fi
  # .zshenv covers every zsh. .zshrc covers an interactive zsh that sets PATH
  # again after .zshenv.
  rc_files=("$rc_dir/.zshenv" "$rc_dir/.zshrc")
else
  # bash reads the first existing login file. Use it instead of shadowing it
  # with a new .bash_profile. Create .bash_profile only when none exists.
  rc_dir="$HOME"
  login_file=""
  for candidate in "$HOME/.bash_profile" "$HOME/.bash_login" "$HOME/.profile"; do
    if [ -e "$candidate" ] || [ -L "$candidate" ]; then login_file="$candidate"; break; fi
  done
  [ -n "$login_file" ] || login_file="$HOME/.bash_profile"
  rc_files=("$login_file" "$HOME/.bashrc")
fi
for rc in "${rc_files[@]}"; do
  if [ -L "$rc" ]; then
    die "a shell startup file is a symlink and is not modified: $rc"
  fi
  if [ -e "$rc" ] && [ ! -f "$rc" ]; then
    die "a shell startup path is not a regular file: $rc"
  fi
done

source_version="$(read_source_version)" || die "cannot read the source version"
[ -n "$source_version" ] || die "the source version is empty"

# --- Plan and confirmation ----------------------------------------------------
info "Crew guided installer"
info "  shell:        $shell_name ($shell_path)"
info "  source:       $source_dir"
info "  environment:  $venv_dir"
info "  command link: $link_path"
if [ "$assume_yes" != 1 ]; then
  if [ -t 0 ]; then
    printf 'Proceed with this installation? [y/N] '
    read -r answer || answer=""
    case "$answer" in
      y|Y|yes|YES|Yes) ;;
      *) info "Cancelled; no changes were made."; exit 1 ;;
    esac
  else
    die "confirmation is required in a non-interactive shell; run again with --yes"
  fi
fi

# --- Create, retain, or refuse the environment --------------------------------
if [ -d "$venv_dir" ]; then
  info "Using existing environment: $venv_dir"
  if environment_owned; then
    info "Retained existing Crew $source_version installation"
  else
    die "the existing environment is not a compatible Crew installation from this source; it is preserved: $venv_dir"
  fi
else
  mkdir -p "$(dirname "$venv_dir")" || die "cannot create the environment parent directory"
  info "Creating environment: $venv_dir"
  "$python_bin" -m venv "$venv_dir" || die "cannot create the virtual environment: $venv_dir"
  info "Installing Crew $source_version from $source_dir"
  if ! "$venv_dir/bin/python" -m pip install --disable-pip-version-check "$source_dir"; then
    rm -rf -- "$venv_dir"
    die "Crew installation failed; no command link or PATH entry was created"
  fi
fi

if [ ! -x "$venv_dir/bin/crew" ]; then
  die "the installed entrypoint is missing: $venv_dir/bin/crew"
fi
entrypoint_version="$("$venv_dir/bin/crew" --version 2>/dev/null || true)"
[ "$entrypoint_version" = "$source_version" ] || die "the installed entrypoint version does not match the source version"

# --- Command link -------------------------------------------------------------
mkdir -p "$bin_dir" || die "cannot create the command link directory: $bin_dir"
if [ -L "$link_path" ] && [ "$(readlink "$link_path")" = "$venv_dir/bin/crew" ]; then
  info "Command link already correct: $link_path"
else
  if [ -e "$link_path" ] || [ -L "$link_path" ]; then
    die "the command link already exists and is not this installation's link: $link_path"
  fi
  ln -s "$venv_dir/bin/crew" "$link_path" || die "cannot create the command link: $link_path"
  info "Created command link: $link_path"
fi

# --- PATH configuration -------------------------------------------------------
configured=0
if fresh_shell_finds_crew; then
  info "A fresh $shell_name shell already finds crew; no startup file change"
else
  for rc in "${rc_files[@]}"; do
    mkdir -p "$(dirname "$rc")" || die "cannot create the startup file directory: $(dirname "$rc")"
    remove_marker_block "$rc" || die "cannot update the startup file: $rc"
    line="export PATH=\"$(shell_double_quote "$bin_dir"):\$PATH\""
    printf '\n%s\n%s\n' "$MARKER" "$line" >> "$rc" || die "cannot update the startup file: $rc"
    info "Added the Crew PATH entry to $rc"
    configured=1
  done
  if ! fresh_shell_finds_crew; then
    die "the PATH entry was added but a fresh $shell_name shell still cannot find $link_path; add $bin_dir to PATH manually"
  fi
fi

# --- Result -------------------------------------------------------------------
info ""
info "Crew $source_version is installed."
info "  command: $link_path"
info "  environment: $venv_dir"
if [ "$configured" = 1 ]; then
  info "Shells that are already open do not have the new PATH entry."
  if [ "$shell_name" = zsh ]; then
    info "  Reload with: exec zsh    (or open a new terminal)"
  else
    info "  Reload with: exec bash   (or open a new terminal)"
  fi
fi
info "Run 'crew --help' to start."
