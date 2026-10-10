# Pure flow engine

`crew.flow.next_tasks(config, flow_name, snapshot, evidence, *, item_id)` plans one item without IO.
It does not read files, call Herdr, save state, dispatch workers, or execute expressions.
Configuration must be a `CrewConfig`. The engine validates its fields and references again.
Invalid input returns `blocked`, a reason, and no tasks.
The examples create no global or project configuration.

## Inputs

- `item_id` and `flow_name` must be nonempty ASCII identifiers. They permit letters, digits, underscores, and hyphens.
- The flow must exist and contain steps.
- `snapshot.tasks` maps deterministic task IDs to records.
- Supported statuses are `queued`, `running`, `done`, `failed`, `blocked`, and `paused`.
- Optional `team`, `role`, `step`, and `round` values must match the ID.
- Dispatch metadata is permitted. Pause/stop requests or uncertain reconciliation block progress, even for a done task.
- Optional `snapshot.decisions` maps decision IDs to `approved`, `pending`, or `rejected` records.
- Only exact `approved` status authorizes a user gate.
- Missing, pending, or rejected decisions return `user_decision`. Malformed decisions return `blocked`.
- `evidence` maps task IDs to nonempty artifact text. Every completed role needs evidence, including roles without gates.

The caller must verify artifact ownership, freshness, and consistency before it supplies text.
This module cannot establish those filesystem facts.
An artifact without task state blocks progress instead of proposing a repeat task.

The engine ignores tasks, decisions, and evidence for other item/flow prefixes.
For the current prefix, records outside reachable history block progress.
This prevents old rounds or future approvals from bypassing prerequisites.
Evidence alone does not mark a task done.
Inputs remain unchanged. Outputs contain no mutable references to input objects.

## Outputs and ordering

`FlowResult` is an immutable dataclass.
It contains `status`, an immutable `tasks` tuple, `reason`, and optional `decision_id`.

| Status | Meaning |
| --- | --- |
| `ready` | One `FlowTask` proposal with `id`, `team`, `role`, `step`, and `round`. |
| `waiting` | An existing task is queued or running. No repeat proposal. |
| `blocked` | Invalid data, failed/paused task, uncertainty, or unmet gate without a loop. |
| `user_decision` | Explicit approval is required. No task proposal. |
| `escalation` | Retry limits are exhausted. User intervention is required. Approval alone does not reset the result. |
| `complete` | All reachable roles and gates passed. No repeat proposal. |

Steps follow YAML list order. Roles follow `Team.roles` order, not mapping order.
Teams require nonempty unique roles and handoffs for each role.
Each role must complete before the engine proposes the next role.

Task IDs use `<item>:<flow>:s<zero-based-step>:r<round>:role<zero-based-role>`.
Decision IDs use the same stem with the `decision` suffix.
The [flow-state bridge](flow-state-bridge.md) saves proposals before runtime dispatch.

## Gates and evidence

Permitted gate expressions are `<current-team>.result == <TOKEN>` and `<current-team>.verdict == <TOKEN>`.
The spaces shown are required.
A team identifier starts with an ASCII letter or underscore.
Later characters can include letters, digits, underscores, and hyphens.

Result tokens are `CONFIRMED`, `NOT_CONFIRMED`, and `INCONCLUSIVE`.
Verdict tokens are `APPROVED` and `REJECTED`.
Other operators, fields, sources, calls, combined expressions, or tokens block proposals.
The parser uses a regex and comparisons. It does not use `eval`.

The final role's artifact requires one canonical line, such as `Result: CONFIRMED` or `Verdict: APPROVED`.
Case, spaces, token, and full-line spelling must match exactly.
These cases block progress:

- Quoted or indented marker lines.
- Duplicate markers, including identical markers.
- Mixed Result/Verdict lines.
- Malformed markers.
- Artifacts with Markdown fences.

Other prose can surround the canonical line.
Earlier roles can omit the marker. If present, the marker must use the same field and agree with team evidence.
Conflicting reviewer/judge evidence blocks progress and retry.
`INCONCLUSIVE` blocks progress. It does not authorize a repair loop.

## Rounds and bounds

The first round is 0.
A valid negative gate with `loop` repeats the preceding step and the gated step in a new round.
Arbitrary graph targets remain deferred.
A loop requires a preceding step and a gate.
The preceding step cannot loop, require a user decision, or contain a protected action.

`Loop.max_fail_rounds` limits additional repair rounds for that loop.
`Flow.max_fail_rounds` limits additional rounds for the whole item.
Zero permits the first attempt but no repair round.
If another failure exceeds either limit, the engine returns escalation.

The engine calculates counters from reachable evidence.
Identical repeated calls do not increase counters.
Earlier steps outside the repair pair keep their IDs.
Later steps use the latest round.
Historical evidence cannot count as completion in a new round.

## User gates and protected actions

`decision: user` applies before any role in the step.
Approval applies only to the exact step and round.
An older approval does not authorize another round.

`FlowStep.action` permits `push`, `pr`, and `merge` annotations.
Each requires `decision: user`.
Reserved teams `push`, `pr`, `merge`, and `pull-request` also require a gate without an action annotation.
Callers must annotate protected effects for other team names.
The planner does not inspect worker commands or enforce external permissions.
Current runtime integration refuses external action roles, even after planner approval.

## Examples and deferred integration

`examples/flows/qa-bugfix.yaml` defines validation, ordered development, bounded review, and separate approvals for push, PR, and merge.
`readonly.yaml` defines one readonly step without retry.
These are flow templates, not complete global configurations.
Callers must supply matching teams, role order, handoffs, and permissions.
The readonly template requires readonly roles in its supplied team.

The planner remains a pure module.
The accepted [runtime](runtime-cli.md) supplies persistence, dispatch, tick, artifact checks, and decisions through separate modules.
See [acceptance](release-acceptance.md) for independent evidence.
Live smoke and account-backed E2E remain unverified. Earlier integration deferrals described the original planner slice, not the current release.
