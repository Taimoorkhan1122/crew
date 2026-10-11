# Reduced release scope

Updated 2026-10-10. **Five of five items are complete and independently accepted.**
This is the reduced release. The original full plan remains incomplete.
See the [backlog](backlog.md) for future work and revisit conditions.

## Accepted scope

| Item | Accepted behavior | Evidence |
| --- | --- | --- |
| Correctness fixes | Exact Lead-only approval and rejection of duplicate YAML keys. | Independent review; 53 targeted tests, 290 full tests, and independent role/key probes. |
| Native worktrees | Owned worktrees, recorded cwd, pinned base, strict patterns, and serialized mutable dispatch. | Independent correction review; 96 targeted tests, 325 full tests, and 22 independent probes. |
| Safe cleanup | Exact ownership, cooperative controls, retained-resource output, and conservative worktree removal. | Independent review; 158 independent targeted tests, 44 probes, and CLI reports. |
| Run-scoped views | Status/state/report/full/fields isolate two runs without another filter. | Independent validation; selected-run assertions in 76 real CLI checks. |
| Final acceptance | Fake lifecycle, independent full suite, and source-matching wheel/install/entrypoint/defaults verification. | Independent validation; 359 full tests and packaging checks. |

The [acceptance record](release-acceptance.md) identifies commands, exit codes, test conditions, historical failures, and package identity.
The Lead recorded closure after independent acceptance.
The executor used Sol 6.1 low. The independent reviewer/validator used Sol 6.1 medium.
Both workers stopped work after publication.

## Operating boundaries

Explicit tick advances the run.
Mutable worktrees isolate paths; mutable dispatch remains serialized.
Role permissions are cooperative, not OS restrictions.
User decisions, strict configuration, artifact freshness, and ownership checks remain required.

Live Herdr behavior, real project/account E2E, and semantic merge compatibility remain unverified.
External operations and deferred features require separate scope and authorization.
Runtime deliberately retains branches, workspaces, callers, and uncertain resources when cleanup evidence is insufficient.
See [runtime cleanup](runtime-cli.md#conservative-safe-cleanup).

## Evidence history

Full-GSD STOPPED and builder-only 53/290 handoffs are historical checkpoints.
Later independent reviews supplied reduced-scope acceptance.
Public summaries retain the results and limits. Original reports and logs remain private.
They do not establish acceptance of the original full plan.

Documentation changes occurred after executable acceptance.
The archived review inventory describes the earlier snapshot.
The readable CLI presentation is post-acceptance work. Independent review accepted its earlier output selection on 2026-10-11.
The review passed 380 tests and checked terminal output with disposable projects and fake adapters.
The revised default uses TOON in terminals and pipes. Readable output requires `--human`.
Independent review accepted this default revision on 2026-10-11. Its full suite passed 388 tests.
Detailed views retain TOON.
The earlier acceptance record applies to its recorded executable snapshot.
