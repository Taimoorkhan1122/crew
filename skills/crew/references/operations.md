# Operations

Read this reference to create, start, advance, or control a run.
Use the installed syntax. Run `crew <command> --help` when syntax differs.

## 1. Inspect without changes

```sh
crew --version
crew --global-dir <GLOBAL_DIR> --project <PROJECT> config path
crew --global-dir <GLOBAL_DIR> --project <PROJECT> config show
crew --global-dir <GLOBAL_DIR> --project <PROJECT> config validate
crew --global-dir <GLOBAL_DIR> --project <PROJECT> resources
crew --global-dir <GLOBAL_DIR> --project <PROJECT> doctor
```

- `config validate` gives the merge and reference result.
- `resources` gives memory, load, workers, and model quota.
- `doctor` gives prerequisite availability.

If the user asks only to learn about Crew, inspect and explain.
Do not launch live resources for a learning request.

## 2. Initialize the project

Run this step only with authorization.

```sh
CREW_ROLE=lead crew --global-dir <GLOBAL_DIR> --project <PROJECT> project init
```

Initialization preserves existing files.
It activates the copied defaults as the project override.

## 3. Create a frozen run

Run this step only with authorization.

```sh
CREW_ROLE=lead crew --global-dir <GLOBAL_DIR> --project <PROJECT> run new \
  --id <RUN_ID> --flow <FLOW> --items <ITEM1,ITEM2> --groups <N>
```

- `--id` is optional. Runtime generates one when you omit it.
- `--flow` must exist in the merged configuration.
- `--items` is a comma-separated list of unique identifiers.
- `--groups` is between 1 and the item count.
- Identifiers permit ASCII letters, digits, underscores, and hyphens.

## 4. Supply input and inspect

Place each input file under the run ticket directory.
The input path comes from the team handoff `in` value.
Use the task `artifact` field and the assigned Output path.
Do not assume the output path.

```sh
crew --global-dir <GLOBAL_DIR> --project <PROJECT> config show --run <RUN_ID>
crew --human --project <PROJECT> status <RUN_ID>
crew --project <PROJECT> state show <RUN_ID> --full
```

## 5. Start the run

Before start, obtain user acceptance of the configuration.
Also obtain authorization for the live resources.

```sh
CREW_ROLE=lead crew --project <PROJECT> run start <RUN_ID>
CREW_ROLE=lead crew --project <PROJECT> run start <RUN_ID> --controller script
```

`run start` creates a Herdr workspace.
The default controller is the legacy Orchestrator.
It launches the configured Orchestrator one time.
A configured `orchestrator` role is required for the legacy controller.
`--controller script` starts one script runner instead of the Orchestrator.
Runtime saves the controller choice with the start intent before side effects.

## 6. Advance the run

```sh
CREW_ROLE=orchestrator crew --project <PROJECT> tick <RUN_ID>
crew --project <PROJECT> advance <RUN_ID>
```

`tick` is explicit. Crew has no background event driver.
Repeat `tick` after worker progress or a user decision.
`tick` reconciles evidence, applies controls, plans reachable work, and dispatches ready tasks.

`advance` runs the script controller in the foreground.
The runner holds one run lock for its lifetime.
Manual `tick`, `dispatch`, and `ask` cannot compete with a script-controlled run.
Use `advance` for that run, including recovery after a stopped runner.
The runner reports its `status` and `reason` in status and report.
Wait reasons can name a user decision, a pause, or an exact `resource_wait` gate.
An exit code of 0 means completion or a user stop. An exit code of 1 means failure.

## Worker completion

A worker writes its assigned artifact.
The artifact must contain a body and the configured marker on a separate line.
A oneshot worker also needs an exit receipt for its exact launch intent.
The launch wrapper writes the receipt. Never forge or edit a receipt.
Workers do not change authoritative state.
Preserve a completed artifact without changes.

## Decisions

```sh
CREW_ROLE=lead crew --project <PROJECT> decide <RUN_ID> <DECISION_ID> approved
CREW_ROLE=lead crew --project <PROJECT> decide <RUN_ID> <DECISION_ID> rejected
```

Only `CREW_ROLE=lead` resolves a decision.
Present the exact decision ID and options to the user first.
For a question decision, use one listed option as the choice.
The Orchestrator and workers cannot resolve decisions.

## Controls

```sh
CREW_ROLE=lead crew --project <PROJECT> pause <RUN_ID>
CREW_ROLE=lead crew --project <PROJECT> resume <RUN_ID>
CREW_ROLE=lead crew --project <PROJECT> stop <RUN_ID>
CREW_ROLE=lead crew --project <PROJECT> close <RUN_ID> --keep-worktrees
```

A control can target one team with `--team <TEAM>`.
A control command saves intent.
The Orchestrator applies the intent on `tick`.
`pause` prevents new dispatch. It does not suspend a process.
`stop` is cooperative. Runtime retains an active or uncertain process.
`close` uses conservative checks.
It can retain a workspace, a branch, or an uncertain resource.
Thus `closed: false` can be the correct result.
Read the reported reason for each retained resource.

## Retries and state

Runtime does not retry a start or a dispatch automatically.
A saved intent or an uncertain result prevents a fresh retry.
An explicit orchestrator `dispatch` is not a general retry.
It can retry an exact pending worktree registration when no worker launch intent exists.
It never repeats an agent launch after `launch_intent` is saved.
Do not repair `state.json` or `run.yaml` by hand.
Create a new run for a configuration change.

## Status, configuration, and evidence

Keep these items separate:

- Run status and task status.
- Frozen configuration.
- Artifact and receipt evidence.

Use `status`, `state show --full`, and `report` for the applicable view.

## Command handling

Use each command as a separate operation.
Do not build one execute-all block of live commands.
