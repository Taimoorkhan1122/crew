# Locked flow-state bridge

`crew.flow_state.persist_next_tasks(store, config, flow_name, evidence, *, item_id, expected_snapshot)` calls the pure `next_tasks` engine.
It returns the immutable `FlowResult`.
`ready` means one proposal was saved as queued. It does not mean dispatched.
`user_decision` means a pending request was saved, or an existing pending/rejected decision was retained.
This API does not approve or reject decisions.

## Inputs and role

`CREW_ROLE=orchestrator` is required before store IO. Otherwise, the function raises `PermissionError`.
Malformed inputs, stale guards, uncertain controls, and file errors return `blocked`.
Supply these inputs:

- A real `StateStore`.
- A validated `CrewConfig`.
- A plain JSON dictionary snapshot.
- A dictionary of task IDs and verified nonempty artifact text.

The function does not read artifact paths.
Before the call, obtain an authoritative snapshot. Verify evidence ownership, freshness, and consistency against that snapshot.
After a stale result, obtain fresh state and verified evidence.
Do not update the guard alone while retaining old evidence.

## Snapshot guard

Under the store lock, the guard compares the complete authoritative JSON object before planning or writes.
Key order does not matter. Values, list order, missing fields, and types matter.
Boolean `true` differs from integer `1`.
The bridge rejects nonfinite numbers, nonstring keys, non-JSON values, and duplicate file keys.
It makes separate copies of guard, evidence, and configuration before planning.

The guard compares content, not a monotonic version.
It cannot detect a change that returns to identical JSON content.
The caller remains responsible for external freshness.

## Controls and records

Each task requires a supported status. Each decision requires `approved`, `pending`, or `rejected` status.
Unknown metadata remains unchanged.
Known flags `pause_requested`, `stop_requested`, `reconciliation_needed`, `closed`, and `close_attempted` must be booleans.
If present, `close_intent` must be a nonempty string.

Active controls on the run or current item/flow block persistence.
This includes controls on historical closed tasks.
The bridge does not clear controls or reopen work.
Controls for other items/flows remain unchanged and do not block current planning.
Malformed known records still fail closed.
The bridge adds no run schema, status, or lifecycle transition.

## Persistence

Under the same lock, the bridge calls `next_tasks`.
It saves at most one deterministic task with status, team, role, step, and round.
Alternatively, it adds one missing exact decision ID with `status: pending`.
It does not replace existing tasks or decisions.

Repeated calls with current snapshots return waiting/user_decision results without writes.
Repeated calls with stale snapshots block progress.
Concurrent callers with the same guard can commit once. Later callers block.
Blocked, waiting, escalation, and complete results do not write state.
Existing state and unknown metadata remain intact.
Missing decisions use the engine's optional decisions mapping. The bridge adds no other container.

The bridge uses strict JSON decoding under the store lock and the existing atomic writer.
It does not append audit events or save flow counters.
The engine calculates counters from reachable history.
File IO errors return blocked.
An error after atomic replacement can leave the proposal saved.
Before a retry, read authoritative state.

The complete snapshot and whole-run lock favor correctness over concurrent work in unrelated lanes.
State content equality does not protect configuration files or artifacts.

## Integration boundaries

The bridge calls no Herdr, network, subprocess, or protected external operation.
The caller verifies artifacts, worker completion, and approval at external operation boundaries.
See [runtime](runtime-cli.md) for the accepted integration and [acceptance](release-acceptance.md) for independent evidence.
Bridge tests use isolated temporary stores.
Live smoke and account-backed E2E remain outside acceptance.
Earlier integration deferrals described the original bridge slice, not the current release.
