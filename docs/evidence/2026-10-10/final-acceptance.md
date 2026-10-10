# Reduced Crew release independent acceptance

Verdict: APPROVED for the reduced release. No required blocking finding remained.
This public summary retains historical results and limits. Private paths and execution identifiers are excluded.
Original reports, logs, scripts, and manifests are private records. They are not included in this repository.
Cleanup acceptance preceded final validation. Both reviews used the same executable snapshot.

## Source identity

The accepted inventory contained 64 files, excluding environment and cache files.
Source hashes matched before cleanup review, after cleanup review, before packaging, and after final validation.
The reviewer found no added or missing inventoried files.
Source had no Git commit identity at acceptance time. Hashes identified the tested snapshot.
Accepted core behavior, permission and YAML fixes, and native-worktree corrections remained preserved.

## Independent checks

| Validation | Result |
| --- | --- |
| Cleanup-focused suite | Exit 0; 158 tests passed in 32.97 seconds. |
| Cleanup probes | Exit 0; 44 independent probes passed. CLI status and report also exited 0. |
| Final full suite | Exit 0; 359 tests passed in 33.36 seconds. |
| CLI lifecycle | Exit 0; 76 real CLI subprocess checks passed. |
| Packaging | 11 successful subprocess commands; all exited 0. |

Builder results were separate: 359 full tests passed in 33.68 seconds.
The independent full suite ran once for the unchanged lifecycle and packaging snapshot.
All validation processes ran sequentially and completed. Tests disabled product bytecode and pytest cache writes.

## Exercised behavior

Role-denial checks preserved state bytes. The Lead resolved the pending decision.
Two isolated runs verified status, state, report, full output, and field selection.
Each view excluded records from the other run. Invalid field selection exited 2.

Lifecycle checks used isolated HOME, global configuration, project, and run directories.
Herdr and quota executables were fake fixtures. CLI commands and assigned fixture workers were real subprocesses.
Checks covered initialization, configuration, authorization, dispatch, tick, output, pause, resume, resource queues, stop, and repeated close.
Repeated initialization preserved a sentinel. Pause prevented new launch.
Active tasks and retained workspace resources remained visible in cleanup output.

The wrapped receipt writer produced launch-bound exit evidence for an actual fixture worker.
Pause reconciliation reached task completion with exit 0 and captured process identity.
Close removed eligible fake-owned panes once. Repeated close remained safe.
This combined fake Herdr with actual local worker execution. It did not establish live Herdr acceptance.

Native Git checks used fresh temporary repositories for creation, launch isolation, ownership rejection, and cleanup.
Separate cleanup probes included 15 real temporary Git cases.
These checks did not establish semantic merge compatibility.

## Packaging

An offline build used a separate source copy, existing setuptools 84.0.0, and the project Python 3.14 interpreter.
Existing Python 3.13 pip installed the wheel into a temporary target for the project interpreter.
Installation used `--no-index`, `--no-deps`, `--no-compile`, and `--no-cache-dir`.
The build and installation downloaded no dependencies.

All 16 Python modules and 17 defaults, prompts, and flow files matched reviewed source bytes.
Installed imports and the generated console entrypoint ran outside the source directory.
Installed global-directory selection, packaged templates, and repeated initialization passed.
Existing interpreter dependencies were reused. This did not establish installation on a new offline machine.

Wheel: `crew_runner-0.1.0-py3-none-any.whl`.
SHA256: `a76d8d8c249321030cd97433687b98d7a683a39831c22de675d816b31a95daf5`.
The historical wheel is not distributed with this summary.

## Historical failures

The first lifecycle harness exited 1 after 25 successful CLI checks.
A fake worker rewrote completed output. Runtime correctly blocked the changed fingerprint.
The fixture was corrected to write each output once. All 76 checks then passed with fresh fixtures.
No product fix occurred for this failure.

The first installer exited 1 because the project interpreter lacked pip.
Existing pip then installed the same wheel for that interpreter. No rebuild or dependency installation occurred.
These failed harness and environment commands remain separate from successful validation results.

## Release limits

Explicit tick remains the progress driver. Role permissions and ownership checks are cooperative.
They do not provide an OS security sandbox.
Active or uncertain resources, the caller, dirty or unowned trees, branches, and workspace shells can remain retained.
A oneshot Orchestrator without a dedicated receipt remains retained.
External filesystem changes, Git changes, and ABA races remain outside cooperative locks.

Live Crew dispatch, live process cleanup, account E2E, and semantic merge compatibility remain unverified.
The original full plan remains incomplete. See the [backlog](../../backlog.md) for deferred work and revisit conditions.
