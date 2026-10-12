# Setup

This guide installs the CLI and prepares configuration. Then use [getting started](quickstart.md) to create a run.

For AI-assisted installation, copy the [installation prompt](../README.md#install-with-an-ai-assistant) into an assistant with local terminal access.
Replace the project path before you send it. The prompt covers installation, project initialization, and configuration checks.

TOON is the default output format in terminals and pipes.
Use `--human` for readable outcomes and labeled paths. Use `--toon` to select TOON explicitly.
Detailed views retain TOON. See the [output contract](runtime-cli.md).

## Prerequisites

- Use Python 3.11 or newer. The runtime uses POSIX file locks. This release has macOS verification; Windows support is unverified.
- For live launches, use a compatible Herdr session that is running.
- Install and authenticate each selected agent CLI. Make the CLI available in new Herdr panes.
- Install `quota-axi` 0.1.60. Supply one fresh record for each exact model ID.
  A `through_reset` runway is ready at every minimum. A `projected_exhaustion` runway must meet the minimum.
  A stale, unknown, or unauthenticated record keeps dispatch queued, even with zero minimum runway.
- Install Git for mutable lanes. A readonly flow can use a project without Git.
- Obtain the source directory or a `crew_runner` wheel. This release has no documented package-registry distribution.

The bundled roles select Claude and OpenCode. You can select other CLIs.
Read [agent compatibility](agent-compatibility.md) for the current agent limits and gaps.
Configure the roles that your flow needs. Make sure that your account permits the selected models.

## Guided installation

Clone the repository first. Then run the installer from the checkout:

```sh
git clone https://github.com/Taimoorkhan1122/crew.git "$HOME/crew"
crew_source="$HOME/crew"
cd "$crew_source"
bash scripts/install.sh
```

The installer does these actions:

1. It checks the shell, Python, Git, and the source checkout.
2. It shows the plan and asks for confirmation.
3. It creates a separate virtual environment, or it validates an existing one.
4. It installs Crew from the checkout. The installation is not editable.
5. It links the `crew` command into the user bin directory.
6. It adds a PATH entry to the startup files of zsh or bash, only when a fresh shell cannot find the expected `crew` command in a supported mode. It checks every supported fresh-shell mode again after the change.

The installer makes no system-wide change and does not use `sudo`.
It preserves existing shell settings, project files, and other installations.
It refuses an occupied command link, an unsafe symlink, a source and environment
overlap, an unusable environment, an unsupported shell, and an unsafe path.
It retains a compatible installation from the same source. It refuses a foreign
environment or a Crew installation from a different source and preserves it.
A second run is safe and adds no duplicate PATH line.

Options:

| Option | Effect |
| --- | --- |
| `--yes` | Do not ask for confirmation. |
| `--source DIR` | Use DIR as the source Git checkout. Default: this repository. |
| `--venv DIR` | Use DIR as the virtual environment. Default: `$HOME/.local/share/crew/venv`. |
| `--bin-dir DIR` | Use DIR for the command link. Default: `$HOME/.local/bin`. |
| `--help` | Show the usage text and exit. |

The environment and command link paths can contain spaces and single quotation marks.

### Install sequence

This diagram shows the guided installation sequence.
The installer writes the command link after the environment installation.
It changes the startup file only after a fresh shell check.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Installer as scripts/install.sh
    participant Shell as zsh or bash
    participant Env as Virtual environment
    participant Link as Command link

    User->>Installer: bash scripts/install.sh
    Installer->>Installer: Check shell, Python, Git, and paths
    Installer->>User: Show the plan and ask for confirmation
    User->>Installer: Confirm, or use --yes
    Installer->>Env: Create or validate the environment
    Installer->>Env: Install Crew from the checkout
    Installer->>Link: Link the crew command
    Installer->>Shell: Probe fresh interactive and non-interactive shells
    alt A fresh shell mode cannot find the expected crew command
        Installer->>Shell: Add the PATH entry and probe again
    end
    Installer->>User: Report the reload instruction
```

### Startup files and open shells

The installer writes the PATH entry to the startup file that the selected shell reads.

- zsh reads `$ZDOTDIR/.zshenv`, or `$HOME/.zshenv` when `ZDOTDIR` is not set. It reads this file in login, non-login, interactive, and non-interactive shells. The installer also writes `$ZDOTDIR/.zshrc` (or `$HOME/.zshrc`) so an interactive shell keeps the entry when `.zshrc` sets PATH again. The installer checks login, non-login, interactive, and non-interactive zsh. It refuses the installation when a startup file still overrides PATH after the change.
- bash reads the first existing of `$HOME/.bash_profile`, `$HOME/.bash_login`, or `$HOME/.profile` for login shells. It reads `$HOME/.bashrc` for interactive non-login shells. The installer writes the PATH entry to the effective login file and to `$HOME/.bashrc`. It creates `$HOME/.bash_profile` only when no login file exists. A non-interactive non-login bash shell reads no startup file. Use a login shell or set `BASH_ENV` for that case.
- A macOS login shell runs `path_helper`. This program reads and re-evaluates PATH. It cannot keep the characters `$`, backtick, quotation mark, or backslash. The installer refuses these characters in HOME, the environment path, and the command link path.

A shell that is already open does not have the new PATH entry.
Reload the shell to use Crew in the current terminal:

- zsh: run `exec zsh`, or open a new terminal.
- bash: run `exec bash`, or open a new terminal.

The installer prints the applicable instruction.

## Manual installation

Use a separate environment for the CLI and templates. Set `crew_package` to the wheel or source directory:

```sh
crew_package="/path/to/crew_runner-0.1.3-py3-none-any.whl"
# Local source alternative:
# crew_package="/path/to/crew"

crew_venv="$HOME/.local/share/crew/venv"
python3 --version
python3 -m venv "$crew_venv"
"$crew_venv/bin/python" -m pip install "$crew_package"

export PATH="$crew_venv/bin:$PATH"
crew_global_dir="$crew_venv/share/crew"
crew --version
crew --help
```

The expected version is `0.1.3`.
The global directory must contain `config.yaml`, `roles.yaml`, `teams.yaml`, `axis.yaml`, `flows/`, and `prompts/`.

Add the equivalent PATH entry to your shell configuration.
In a new Herdr pane, use `command -v crew` to confirm CLI availability.
Environment activation in the Lead terminal does not set PATH in new panes.

A normal installation can download dependencies and the build backend.
An offline installation requires local dependencies. The accepted packaging test reused existing dependencies.
That test did not establish installation on a new offline machine.

## Select configuration explicitly

Without `--global-dir`, Crew reads `~/.config/crew`. Package installation does not populate that directory.
These commands select the installed `share/crew` directory:

```sh
crew_project="/absolute/path/to/your/project"

CREW_ROLE=lead crew --human --global-dir "$crew_global_dir" --project "$crew_project" project init
crew --global-dir "$crew_global_dir" --project "$crew_project" config path
crew --global-dir "$crew_global_dir" --project "$crew_project" config show
crew --global-dir "$crew_global_dir" --project "$crew_project" config validate
```

Initialization creates `.crew/project.yaml` and copies the templates and prompts without replacing existing files.
The copied split files become the project override. Put model and project changes in the `.crew/` files.
Keep the installed templates as the base configuration. Prompt paths are relative to the project's `.crew/` directory.
See the [overlay and pinning contract](config-migration.md#project-initialization-and-pinning).

For source inspection, use `--global-dir "/path/to/crew/defaults"`. See [development](development.md) for the development environment.

## Example Codex overlay for a small readonly run

Merge this fragment into `.crew/project.yaml`. Preserve existing project settings.
The fragment changes the two roles for a readonly run. It removes external tool requirements for that team.

```yaml
roles:
  orchestrator:
    cli: codex
    herdr_kind: codex
    model: gpt-6.1-sol
    effort: medium
    mode: interactive
    args: [-m, '{model}', -c, 'model_reasoning_effort="{effort}"']
    axis: []
  readonly-worker:
    cli: codex
    herdr_kind: codex
    model: gpt-6-luna
    effort: low
    mode: oneshot
    args: [exec, --skip-git-repo-check, --sandbox, workspace-write, -m, '{model}', -c, 'model_reasoning_effort="{effort}"', '{prompt}']
    axis: []
teams:
  readonly:
    axis: []
```

The worker inherits `readonly: true`. It can inspect the project and write its assigned report.
The workspace-write sandbox permits the report write. The readonly instruction is cooperative; it is not an OS restriction.

Use Sol 6.1 low for implementation. Use Sol 6.1 medium for independent review. Use Luna for smaller readonly tasks.
If you change a role's CLI, set compatible native arguments and launch mode.
A model change alone does not configure another CLI.

## Readiness check

```sh
crew --global-dir "$crew_global_dir" --project "$crew_project" config validate
crew --global-dir "$crew_global_dir" --project "$crew_project" doctor
crew --global-dir "$crew_global_dir" --project "$crew_project" axi check
crew --global-dir "$crew_global_dir" --project "$crew_project" resources
```

Doctor tests executable availability and reads integration status.
It does not configure Herdr, establish live dispatch, or change restore settings.
AXI check reports configured tools. `axi install` reports an approval request; it does not execute an installer.

Resources must report ready for the selected model.
If resources are not ready, inspect quota, memory, load, and worker limits.

Use `herdr --help` to inspect the installed interface.
The configuration key `herdr_session` selects the session. Its default value is `default`.
The user must respond to permission and authentication dialogs. Agents must not respond for the user.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Configuration is missing or invalid | Select the packaged `--global-dir`. Read `config path`. Execute `config validate`. |
| Prompt is missing | Initialize the project. Inspect the path under `.crew/`. |
| Orchestrator cannot find `crew` | Confirm PATH in a new Herdr shell. |
| Agent cannot start | Inspect CLI availability, authentication, arguments, model access, and Herdr session. |
| Worker stays queued | Inspect resources, tool records, quota, controls, and mutable dispatch limits. |
| Start or launch is uncertain | Read `state show RUN_ID --full`. Inspect resource identity before a retry. |
| Close retains resources | Inspect `cleanup`. Active processes, branches, and workspace shells can remain. |

Setup is complete when configuration validates and the launch environment can find the required executables.
Before a live run, obtain user acceptance of configuration and resource changes.

## Updates after installation

An older CLI does not have `update`. First install an updater-enabled release with the manual installation steps above.
Keep the development checkout separate from the installed working checkout.
Crew uses a published stable release. It does not use a branch or an unpublished tag as a substitute.

```sh
crew update --check
crew --human update --check
crew update
```

The installed package metadata selects the working source and virtual environment.
The current directory and `--project` do not select update targets.
Read the [update requirements and recovery procedure](runtime-cli.md#explicit-self-update) before an update.
