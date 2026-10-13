---
name: crew
description: Set up, configure, and supervise Crew runs for local agent teams. Use for Crew YAML edits, run start and tick, status review, decisions, controls, cleanup, and launch troubleshooting.
metadata:
  short-description: Run and supervise Crew agent teams
---

# Crew

Crew runs configured agent teams through Herdr.
The Lead supervises work and presents user decisions.
One Orchestrator, or one script controller, advances each run.
Workers produce assigned artifacts.

## Identify the setup first

These commands do not read configuration:

```sh
crew --version
crew --help
crew --global-dir <GLOBAL_DIR> --project <PROJECT> config path
```

`crew --version` gives the installed version.
`config path` prints the global directory and the project file.
With `--run`, `config path` also prints the run file.
`config path` does not read YAML.

`config show` and `config validate` read the complete selected global configuration.
They require a `<GLOBAL_DIR>/config.yaml` file.
A project override cannot replace a missing `config.yaml`.
`config show --run <RUN_ID>` reads the frozen run configuration instead.

Use the user's existing global directory when the user has one.
Otherwise select the installed `share/crew` templates with `--global-dir`.
Do not overwrite the global files.
The installer links `crew` into the user bin directory.
That link can be a symlink, so resolve it before you derive the prefix.
For a virtual-environment install, this procedure resolves the command and finds the templates:

```sh
crew_bin=$(command -v crew)
crew_real=$(realpath "$crew_bin" 2>/dev/null || readlink -f "$crew_bin" 2>/dev/null || echo "$crew_bin")
crew_prefix=$(dirname "$(dirname "$crew_real")")
crew_global_dir="$crew_prefix/share/crew"
```

This procedure assumes a standard virtual-environment layout.
A wrapper script or a non-virtual-environment install can differ.
When the layout differs, ask the user for the directory that contains `config.yaml`.
Before `config show` or `config validate`, confirm the selected directory contains `config.yaml`:

```sh
test -f "$crew_global_dir/config.yaml"
```

Then run `config validate` and `config show` with that directory.

Match the installed version before you use a documented command.
Run `crew <command> --help` when a command differs from this skill.

TOON is the default output format.
Use `--human` for readable outcomes, paths, and lists.
Detailed views stay TOON: `--fields`, `--full`, `state show`, and `config show`.

## Role routing

`CREW_ROLE` selects the command context for one Crew command.
The role is command context.
It is not a privilege or a security boundary.
Code with the same operating system user can bypass it.

| Context | Use |
| --- | --- |
| Lead (`CREW_ROLE=lead`) | Initialize projects, create runs, record controls, and resolve decisions. Only Lead resolves decisions. |
| Orchestrator (`CREW_ROLE=orchestrator`) | Advance a run with explicit `tick`, and dispatch work. |
| Worker | Read the assigned input and write the assigned artifact. Runtime sets this context. |

A run can use `run start --controller script` instead of the legacy Orchestrator.
Then `crew advance <RUN_ID>` runs the script controller.
Manual `tick`, `dispatch`, and `ask` do not compete with a script-controlled run.
Only Lead resolves a decision. The runner observes it on the next poll.

A user can also ask the current agent to do ordinary work.
Do not refuse ordinary work from a role name alone.
Do not invent the user's answer for a decision.
Present the exact decision ID and options to the user.

## Report status in parts

Report these items separately. Do not merge them.

- Run status: flow, items, and decisions.
- Task status: queued, running, blocked, done, paused, or failed.
- Process status: active or unknown panes and workspace.
- Cleanup status: retained resources and removed resources.

## Read one reference

Read only the reference that matches the task.

| Situation | Reference |
| --- | --- |
| Change configuration or inspect the merge | [references/configuration.md](references/configuration.md) |
| Create, start, advance, or control a run | [references/operations.md](references/operations.md) |
| Diagnose a failed or uncertain launch | [references/troubleshooting.md](references/troubleshooting.md) |

## Live-run limits

Crew changes local state and live resources.
Give the user a plan before you start live resources.
Obtain user acceptance of the configuration and authorization for the live resources.
Do not repeat approval for an action that the user already authorized.
If the user asks only to learn about Crew, inspect and explain.
Do not launch live resources for a learning request.
