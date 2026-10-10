# Acceptance record — 2026-10-10

The designated Sol 6.1 medium reviewer/validator independently approved the reduced release.
[Release scope](remaining-scope.md) lists accepted behavior. The [backlog](backlog.md) lists remaining verification and engineering work.

## Recorded gates

| Gate | Independent evidence | Verdict |
| --- | --- | --- |
| Correctness fixes | 53 targeted tests, 290 full tests, 40 edge checks; role denial and duplicate-key rejection. | APPROVED |
| Native-worktree correction | 96 targeted tests, 325 full tests, 22 probes; rejected formatter indexing and attributes. | APPROVED |
| Safe cleanup | 158 targeted tests in 32.97 seconds, 44 probes, and actual CLI status/report output. | APPROVED |
| Final acceptance | 359 full tests in 33.36 seconds, 76 real CLI checks, 11 successful packaging commands. | APPROVED |

All accepted validation commands returned exit code 0.
Executor results for 158 targeted tests and 359 full tests are separate builder evidence.
The Lead closed the reduced release after report review and worker idle verification.

## What was exercised

Herdr and quota operations used fake adapters/executables.
Git used fresh temporary repositories.
CLI commands and assigned test workers were real subprocesses.
Tests used isolated HOME, configuration, project, and run directories.

Two-run status/state/report/full/field checks found no records from another run.
Lifecycle checks covered roles, decisions, receipts, controls, resource queues, completion, and repeated stop/close.
Role denial left state unchanged.
Cleanup probes covered identity changes, ownership changes, the caller, active writers, dirty/unowned worktrees, queued worktrees, and lost results.

Live Herdr dispatch/cleanup and account E2E remain unverified.
Installation on a new offline machine and semantic merge compatibility also remain unverified.

## Source and package identity

The accepted snapshot contained 64 inventoried files before documentation changes.
Their hashes stayed unchanged through independent review and final validation.
All 33 packaged Python/default/prompt/flow files matched the reviewed source.

Wheel: `crew_runner-0.1.0-py3-none-any.whl`.

SHA256: `a76d8d8c249321030cd97433687b98d7a683a39831c22de675d816b31a95daf5`.

An offline build used a separate source copy and existing setuptools 84.0.0.
Existing pip installed the wheel into a temporary target with the project interpreter and existing dependencies.
All modules and the generated entrypoint ran outside the source directory.
Installed global-directory selection, packaged templates, and repeated initialization passed.
Initialization preserved a sentinel.

Later documentation changes affect README/docs text.
The archived snapshot remains historical. Code, tests, defaults, and wheel bytes remain unchanged.

## Historical failures and public summaries

The first lifecycle attempt failed because a fake worker rewrote completed output.
Runtime correctly blocked the changed fingerprint.
A corrected helper repeated all 76 checks with fresh test inputs.

The first installer failed because the project interpreter lacked pip.
Existing Python 3.13 pip then installed the same wheel.
The exit code 1 records remain identified as failures.

The following public summaries retain results and limits. They exclude private paths and execution identifiers:

- [Independent cleanup review](evidence/2026-10-10/cleanup-review.md).
- [Independent final acceptance](evidence/2026-10-10/final-acceptance.md).

The original logs, scripts, manifests, and reports are private records. They are not included in this repository.
The summaries identify historical results. They do not supply downloadable test logs or release artifacts.
At acceptance, source had no Git commit identity. The source inventory identified the tested snapshot.
