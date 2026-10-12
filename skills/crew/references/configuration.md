# Configuration

Read this reference to change Crew configuration or to inspect the merge.
This reference describes Crew version 0.1.2.

## Layout

| Location | Content |
| --- | --- |
| Global directory | Global defaults: `config.yaml`, `roles.yaml`, `teams.yaml`, `axis.yaml`, `flows/`, and `prompts/`. Default: `~/.config/crew`. Use `--global-dir` to select another directory. |
| Installed templates | The same file set under the installation `share/crew` directory. |
| Project `.crew/` | Project override files and editable prompts. |
| Run `.crew/runs/<id>/` | Frozen `run.yaml` configuration and authoritative `state.json` state. |

Installation does not populate the user global directory.

## Merge order

The loader applies four layers in this exact order:

1. Global defaults.
2. Project split files in `.crew/`.
3. `.crew/project.yaml`.
4. A supported explicit override. The loader accepts this override parameter. `run new` does not supply it.

Each later layer overrides an earlier layer.
The loader validates the merged result one time.
`run new` accepts `--flow`, `--items`, and `--groups`. These flags select run metadata. They supply no general configuration override.

## Project split files

A missing project file inherits the global values.
A partial mapping overrides the specified fields only.

| File | Shape |
| --- | --- |
| `.crew/config.yaml` | Top-level settings. |
| `.crew/roles.yaml` | Role-name mappings. No outer `roles` key. |
| `.crew/teams.yaml` | Team-name mappings. No outer `teams` key. |
| `.crew/axis.yaml` | Tool-name mappings. No outer `axis` key. |
| `.crew/flows/<name>.yaml` or `.yml` | One flow definition for that name. |
| `.crew/project.yaml` | Full-schema overlay. It wins over the split files. |

Merge rules:

- A missing file or field keeps the inherited value.
- A list replaces the complete list. The loader does not append items.
- A supplied `handoffs` mapping replaces the whole mapping.
- The flow name is the file stem. Two files with the same stem in one layer fail.
- The same flow name in different layers is an override.

## Validation

The loader rejects duplicate YAML keys and unknown fields.
It validates role, team, and flow references.
It checks each project split path, including the flows directory and each flow file.
It rejects symlinks and path escape.
The schema checks many fields.
It restricts `mode` to `interactive` or `oneshot`.
It does not check CLI, provider, or model support.

## Prompts

A project prompt path is relative to `.crew/`.
An absolute prompt path fails.
A missing prompt fails at launch.
Run `crew project init` to copy the default prompts.

## Initialization and pinning

`crew project init` copies the global templates into `.crew/`.
It does not replace an existing file.
The copied split files become the project override at once.

Warning: a copied value pins the project against a later global change.
Set the project value again, or remove the copied file, to inherit the global change.

Initialization preserves existing files.
It activates the copied defaults immediately.

## Frozen runs

`crew run new` freezes the merged configuration in `run.yaml`.
It records a configuration digest in state.
A later global or project change does not change that run.
Create a new run to apply a configuration change.
Runtime refuses progress after a frozen configuration change.

Use `config show` for the project configuration.
Use `config show --run <RUN>` for the frozen run configuration.
These outputs differ after a global or project change.

## Change configuration with config set

Use `crew config set <dot.path>=<value> --scope <scope>`.
The value parses as YAML.
Each path part matches `[A-Za-z_][A-Za-z0-9_-]*`.

Supported scopes:

| Scope | Effect |
| --- | --- |
| `project` (default) | Edit `.crew/project.yaml`. The loader validates the result before replacement. |
| `global` | Edit a global file. `roles`, `teams`, and `axis` write their own section file. `flows.<name>.<field>` writes `flows/<name>.yaml`. Other keys write `config.yaml`. |
| `run` | Edit the frozen `run.yaml`. The run must have no start, workspace, tasks, or decisions. `--run <RUN_ID>` is required. |

Example run override, before the run starts:

```sh
crew --global-dir <GLOBAL_DIR> --project <PROJECT> config set \
  roles.<ROLE>.model=<MODEL> --scope run --run <RUN_ID>
```

Do not edit `run.yaml` or `state.json` directly.
A direct `run.yaml` edit changes the frozen configuration and breaks the configuration digest.
A direct `state.json` edit corrupts authoritative state and the ownership records.

## Role changes

Verify the CLI and the arguments when you switch an agent.
A `model` or `effort` change alone does not set native CLI flags.
The role `args` list supplies the native flags.
`mode` selects `interactive` or `oneshot`.
A `oneshot` role needs `{prompt}` in `args`.
The schema checks `mode`. It does not check CLI compatibility.
Check the CLI, the provider, the model, the `mode`, and the arguments together.

`role.skills` is a configuration value only.
Runtime and launch code do not install, load, or enforce it.

## Readonly limits

`readonly` is cooperative.
It covers the configured write gate only.
It is not an operating system isolation boundary.
