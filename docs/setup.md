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
- Supply a ready `quota-axi` record for each exact model ID. Unknown quota keeps dispatch queued, even with zero minimum runway.
- Install Git for mutable lanes. A readonly flow can use a project without Git.
- Obtain the source directory or a `crew_runner` wheel. This release has no documented package-registry distribution.

The bundled roles select Claude and OpenCode. You can select other CLIs.
Configure the roles that your flow needs. Make sure that your account permits the selected models.

## Install

Use a separate environment for the CLI and templates. Set `crew_package` to the wheel or source directory:

```sh
crew_package="/path/to/crew_runner-0.1.0-py3-none-any.whl"
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

The expected version is `0.1.0`.
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

Initialization creates `.crew/project.yaml`. It copies templates and prompts without replacing existing files.
Put model and project changes in that overlay. Keep the installed templates as the base configuration.
Prompt paths are relative to the project's `.crew/` directory.

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
No published stable release was available on 2026-10-11. Crew does not use a branch or an unpublished tag as a substitute.

```sh
crew update --check
crew --human update --check
crew update
```

The installed package metadata selects the working source and virtual environment.
The current directory and `--project` do not select update targets.
Read the [update requirements and recovery procedure](runtime-cli.md#explicit-self-update) before an update.
