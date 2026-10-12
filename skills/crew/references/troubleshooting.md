# Troubleshooting

Read this reference to diagnose a failed or uncertain launch.
Default diagnosis is read-only.
Do not start an agent or call a model without explicit user authorization.
A user-authorized live probe is permitted when a read-only check cannot decide.

## Start or launch is uncertain

1. Read the saved state: `crew --project <PROJECT> state show <RUN_ID> --full`.
2. Inspect `workspace`, `start_intent`, `start_uncertain`, and `orchestrator`.
3. Inspect the live pane, tab, and session identity with the Herdr tools.
4. Compare the saved identity with the live identity.

A saved intent prevents a fresh retry.
Do not create another workspace or launch another Orchestrator from an uncertain start.
Report the evidence, the limits, and the recovery choices.

## Configuration checks

`config validate` checks the Crew merge and schema.
It does not check native CLI flags.
Run the installed CLI help before you recommend arguments.
Example commands: `codex exec --help` and `opencode run --help`.

## Agent CLI facts

These facts reflect the inspected CLI help.
Recheck the installed help before you rely on a flag. Flags can change across versions.

- OpenCode exposes `--model` on the `run` and `mini` subcommands only.
- OpenCode has no separate `--effort` flag.
- `opencode run --model` accepts `provider/model#variant`. Use a variant only when the local build supports it.
- `opencode mini --model` accepts `provider/model`.
- A correct `mini` command does not prove that Herdr can deliver a prompt to that agent.
- The Codex setup fragment configures selected roles. It is not an all-role preset.
- The default Claude and OpenCode mix is not universal compatibility.

Verify the CLI, the provider, the model, the `mode`, and the arguments together.

## Worker stays queued

Check these items:

- Memory, load, and worker limits in `resources`.
- The exact-model quota record. An unknown exact model keeps dispatch queued, even with minimum runway zero.
- A saved control intent such as pause or stop.
- The mutable dispatch limit. Runtime serializes mutable dispatch.

## Missing artifact, marker, or receipt

Check these items:

- The task `artifact` field matches the assigned output path.
- The file is regular, has one link, and is not a symlink.
- The file stays inside the run directory.
- The file size is at most 1 MiB.
- The file is newer than the launch.
- The file has a body and the exact marker on a separate line.
- A oneshot task has a fresh receipt for its exact launch intent.

A missing or changed evidence item blocks progress.
Do not invent a marker, a receipt, or a state record.

## CLI path

A new Herdr pane must find `crew`.
Use `command -v crew` in a new pane.
Environment activation in one terminal does not set PATH in new panes.

## Ownership and retained resources

Read the `cleanup` record in `status` or `state show --full`.
`active_tasks`, `orchestrator`, `retained_branches`, and `retained_resources` list the retained items.
Retention is conservative behavior.
It does not prove that runtime removed the resource.
Do not use a blanket `pkill`, `rm`, `reset`, or `delete` command.
Remove a resource only with exact ownership and an applicable recovery step.

## Stop condition

Stop the diagnosis with concrete evidence, the current limits, and the recovery choices.
Do not claim live agent support from configuration alone.
