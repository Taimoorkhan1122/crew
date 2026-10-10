# Getting started

Start with one readonly item. Complete [setup](setup.md) first.
Make sure that new Herdr panes can find the CLI. These commands select a global directory and project root.

## 1. Prepare the project

Set these values for your installation and project:

```sh
crew_venv="$HOME/.local/share/crew/venv"
crew_global_dir="$crew_venv/share/crew"
crew_project="/absolute/path/to/your/project"

CREW_ROLE=lead crew --global-dir "$crew_global_dir" --project "$crew_project" project init
crew --global-dir "$crew_global_dir" --project "$crew_project" config show
crew --global-dir "$crew_global_dir" --project "$crew_project" config validate
```

Review `.crew/project.yaml`. Inspect the worker and Orchestrator roles, prompts, arguments, resource limits, and tools.
If applicable, apply the [Codex example](setup.md#example-codex-overlay-for-a-small-readonly-run). Validate the configuration again.
Initialization preserves existing files.

## 2. Freeze a run and supply its input

```sh
CREW_ROLE=lead crew --global-dir "$crew_global_dir" --project "$crew_project" run new --id demo --flow readonly --items DEMO --groups 1

mkdir -p "$crew_project/.crew/runs/demo/tickets/DEMO"
cat > "$crew_project/.crew/runs/demo/tickets/DEMO/bug.md" <<'EOF'
Read the project's public documentation and describe its structure.
Use repository files only. Write the assigned research report with evidence.
EOF

crew --global-dir "$crew_global_dir" --project "$crew_project" config show --run demo
crew --project "$crew_project" status demo
```

The expected run directory is `.crew/runs/demo/`. It contains fixed `run.yaml` configuration and authoritative `state.json` state.
The readonly team reads `tickets/DEMO/bug.md`.
Runtime places each output under a namespace for its flow, step, round, and role.
Use the task's `artifact` field and assigned Output path.
Do not assume that the output is at `tickets/DEMO/research.md`.

Use a new run ID for another attempt. IDs and items permit ASCII letters, digits, underscores, and hyphens.
`--groups` creates item lanes. It does not permit parallel mutable dispatch.

## 3. Start after user acceptance

Start creates a Herdr workspace and launches the configured Orchestrator.
Before this step, obtain user acceptance of configuration and authorization for the live resources:

```sh
CREW_ROLE=lead crew --project "$crew_project" run start demo
crew --project "$crew_project" status demo
```

The Orchestrator receives the run path and tick command.
It dispatches workers and advances the run. The Lead supervises work and presents decisions.

## 4. Advance in the Orchestrator context

Explicit tick advances the run. In the Orchestrator context, use the project root as the current directory:

```sh
CREW_ROLE=orchestrator crew tick demo
```

Repeat tick after worker progress or a user decision.
Tick reconciles owned evidence and dispatches reachable work. Crew has no background event driver.

The worker must write a report body. It must put the exact `READONLY DONE` marker on a separate line.
A oneshot task also requires an exit receipt for its exact launch intent.
Missing, changed, or mismatched evidence prevents progress. Preserve completed artifacts unchanged.

## 5. Inspect selected-run output

In the Lead context, use these commands:

```sh
crew --project "$crew_project" status demo
crew --project "$crew_project" state show demo --full
crew --project "$crew_project" report demo --full
crew --project "$crew_project" status demo --fields run_id,tasks
```

The output must contain records for `demo` only.
Inspect the research artifact for useful content and the completion marker.
Read commands use TOON. Exit codes are 0 for success, 1 for runtime/configuration errors, and 2 for argument errors.

If a decision appears, present its exact ID and options to the user.
After the user supplies a choice, the Lead relays that choice:

```sh
CREW_ROLE=lead crew --project "$crew_project" decide demo DECISION_ID CHOICE
```

Replace `DECISION_ID` and `CHOICE` with the pending ID and user's answer.
The Orchestrator, worker, and an unset role cannot resolve decisions.

## 6. Request controls and inspect their effects

```sh
CREW_ROLE=lead crew --project "$crew_project" pause demo
CREW_ROLE=lead crew --project "$crew_project" resume demo
CREW_ROLE=lead crew --project "$crew_project" stop demo
CREW_ROLE=lead crew --project "$crew_project" close demo --keep-worktrees
crew --project "$crew_project" status demo --full
```

Use these commands as separate operations. Do not execute the block as a single procedure.
Lead commands save control intent. The Orchestrator applies controls on tick.
Pause prevents new dispatch. Stop is cooperative; output still reports active processes.
Close uses conservative checks and permits repeated requests.
Inspect `cleanup.active_tasks`, retained resources, `stopped`, and `closed`.

A workspace, branch, or uncertain resource can remain. Thus, `closed: false` can be an expected result.
Runtime retains the Orchestrator that executes the command. Read the reported reasons for retained resources.

## Next steps

Before a mutable run, configure project-relative Git roots, base selection, and a supported worktree pattern.
See [runtime worktrees](runtime-cli.md#native-worktree-creation-and-launch) and [configuration migration](config-migration.md).

The QA flow requires ticket inputs, selected CLIs/tools, and the necessary verification environment.
Runtime refuses external push, PR, and merge actions, even after planner approval.
See the [backlog](backlog.md) for future work.
