# Agent compatibility

Date: 2026-10-12.

This page records agent compatibility limits for release 0.1.2.
Crew dispatches a configured role to an agent CLI through Herdr.
Valid configuration does not prove that an agent works in a live run.
This page separates confirmed source limits, live acceptance gaps, and historical smoke results.
It does not claim a current provider CLI incompatibility without evidence.

## Role configuration

A role selects one agent and its launch behavior.
The role fields are `cli`, `herdr_kind`, `model`, `effort`, `args`, `mode`, `prompt`, `readonly`, `skills`, and `axis`.
The `mode` field is `interactive` or `oneshot`.
The default roles are in [roles.yaml](../defaults/roles.yaml).

An interactive role needs compatible Herdr integration.
Herdr starts the agent and delivers the prompt.
A oneshot role executes the configured CLI command.
A wrapper writes a private exit receipt after the command stops.
A model change alone does not configure another CLI.
After a CLI change, set compatible arguments, mode, and tool requirements.
See the [Codex overlay example](setup.md#example-codex-overlay-for-a-small-readonly-run).

Multiple roles can use different agents.
A multiple-agent architecture does not prove that each agent works.
The user must check the CLI flags, provider, model, and mode.
The schema checks allowed `mode` values. It does not verify compatibility with the selected agent CLI.

## Confirmed source limits

- Role `skills` values exist in the schema and configuration only. Runtime and launch code do not install, load, or enforce them. Enforcement needs a later scoped task.
- Role permissions are cooperative. They do not restrict code that runs as the same operating system user.
- An AXI write gate is either a CLI flag or a `<NAME>_READ_ONLY` variable. Launch code supports these two forms only. It does not translate a native tool policy. See [axis.yaml](../defaults/axis.yaml).
- Readonly enforcement covers the configured write gate only. It is not an operating system isolation boundary.
- `quota-axi` needs a ready record for the exact model. An unknown model can stay queued, even with minimum runway zero. See [resources.py](../crew/resources.py).
- The schema does not verify CLI flags, provider availability, or model support. The user must check compatibility for the selected `mode`.

## Agent support and gaps

This table separates existing support from unverified acceptance.

| Agent | Existing support | Remaining gap | Acceptance condition |
| --- | --- | --- | --- |
| Codex | A setup fragment configures the `orchestrator` and `readonly-worker` roles. It removes external tool requirements for the `readonly` team. | No complete Codex preset exists for all roles. The fragment is not a fresh integrated Crew lifecycle acceptance. | An authorized isolated run uses a Codex role profile through a complete readonly lifecycle. |
| OpenCode | Default oneshot roles use OpenCode: `qa-reviewer`, `qa-verifier`, and `qa-judge`. External development writer and reviewer runs succeeded outside Crew. | No complete all-role OpenCode preset exists. External runs do not verify Crew-managed dispatch, tick, receipt, or cleanup. | An authorized isolated run uses a configured OpenCode worker through Crew dispatch and reconciliation. |

The `--agent` names and the `provider/model#effort` value need compatible local definitions, variants, and authentication.
The defaults use these values for the `qa-reviewer`, `qa-verifier`, and `qa-judge` roles.

## Shared acceptance gaps

Both agent CLIs lack fresh isolated integrated coverage of these items:

- Crew run start and interactive prompting.
- Real oneshot exit receipt and artifact completion.
- Quota, authentication, and model selection.
- Failure, retry, stop, close, and ownership behavior.

These items are unverified acceptance. They are not confirmed defects.

## Historical smoke

The [Herdr smoke findings](herdr-notes.md) record a test on 2026-10-08.
Claude Haiku and OpenCode interactive prompts returned their expected markers.
OpenCode oneshot execution returned its expected marker.
That test used direct Herdr interaction.
It did not establish Crew runtime dispatch, process cleanup, account E2E, or support for other hosts.

## Deferred revisit conditions

Revisit this work when the user authorizes disposable live Herdr sessions, topology changes, and cleanup.
The authorization must also permit agent API and authentication use.
Start with a Codex readonly role and a configured OpenCode worker.
Then test a whole-role profile.
This task does not execute a live run.

## Related documents

- [Configuration model](config-migration.md#configuration-model)
- [Project initialization and pinning](config-migration.md#project-initialization-and-pinning)
- [Runtime structure](development.md#runtime-structure)
- [Task lifecycle](runtime-cli.md#task-lifecycle)
- [Herdr smoke findings](herdr-notes.md)
- [Backlog](backlog.md)
