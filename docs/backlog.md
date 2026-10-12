# Backlog

Updated 2026-10-12. The five-item [reduced release](remaining-scope.md) is accepted.
These items remain deferred. They do not authorize execution or block the accepted scope.

## Validation and migration candidates

| Item | Current gap | Revisit condition |
| --- | --- | --- |
| Live Herdr acceptance | Tests passed. Live Crew launch and process cleanup remain unverified. | User authorizes an isolated session, topology changes, and cleanup. |
| [Agent compatibility](agent-compatibility.md) | Codex and OpenCode lack fresh integrated Crew lifecycle coverage. | User authorizes disposable live sessions, agent API and authentication use, and cleanup. |
| Live self-update | Independent offline review accepted the updater on 2026-10-11. No stable release or live install was verified. | Publish an authorized stable release, then authorize a disposable live upgrade check. |
| Installation on a new machine | Wheel verification reused existing interpreter dependencies. | A release needs a clean supported host with authorized dependency setup. |
| Legacy overlay migration | Legacy overlays can contain unsupported absolute repositories and an absolute `{ticket}` pattern. | User authorizes migration before a mutable run. |
| Account-backed or real-ticket E2E | Real account inputs, environments, and execution remain unverified. | Inputs, access, and execution authorization are available. |
| Additional host support | macOS verification and POSIX code do not establish support for other hosts. | A deployment requires another OS with scoped checks. |

## Deferred engineering

| Item | Revisit condition |
| --- | --- |
| Event subscriptions and `events_lost` reconciliation | Explicit tick limits observed progress. |
| Sidebar metadata, Herdr views, and team layouts | Selected-run CLI output is insufficient. |
| Source ownership/conflict analysis and parallel mutable scheduling | Native worktrees and serialized dispatch measurably limit throughput. |
| Process suspension/resumption | Cooperative pause cannot meet a demonstrated requirement. |
| [Optional nono worker sandbox](#optional-nono-worker-sandbox) | A run needs OS enforcement of worker filesystem or network limits. |
| Plugin files/hooks, marketplace, and topology/server changes | A real run needs separately authorized changes. |
| Environment up/down automation | An authorized verification task needs an environment. |
| Push/PR/merge and tool-level write enforcement | External actions have explicit scope and authorization. Current action roles remain refused. |
| AXI home-view capture and automatic installation | Onboarding needs these features. Installation requires approval. |
| Session adoption, quota watcher, and restore automation | Exact identity checks and explicit retries are insufficient. |
| Config/state transactions and editor coordination | Concurrent editing needs stronger guarantees. |

## Optional nono worker sandbox

Status: proposed; deferred. Documentation review indicates a possible integration. Crew compatibility remains unverified.

Crew role permissions are cooperative. [nono](https://nono.sh/) provides process sandboxing with Landlock on Linux and Seatbelt on macOS.
Its [security model](https://nono.sh/docs/cli/internals/security-model) describes filesystem, network, and command policies.
The supervisor and profiles remain trusted. These controls do not provide a separate kernel or VM boundary.

Revisit when a run requires OS enforcement and an isolated compatibility test has authorization.
Start with an optional CLI wrapper for a readonly oneshot worker. Keep installation and profile changes explicit.
Crew currently launches through [Herdr](../crew/herdr.py) and [role launch commands](../crew/launch.py).
Apply the sandbox to the actual agent launched inside the pane. Wrapping the Crew controller alone cannot establish worker isolation.

Proposed scope:

- Select a reviewed nono version and profile per role. Save their identities with the frozen run configuration.
- Permit repository reads and writes to the assigned report. Exclude unrelated projects, private files, and Crew control state.
- Permit required agent API traffic. Define credential, network, and environment grants explicitly.
- Keep trusted receipt writing and supervision outside worker write authority.
- Preserve launch identity, exit status, artifact verification, and conservative cleanup when the supervisor adds processes.
- If a role requires sandboxing, refuse launch when enforcement is unavailable. Report the reason without an unsandboxed retry.

Acceptance requires allowed report writes, denied unrelated reads and writes, denied network destinations, and correct descendant enforcement.
Use temporary secrets and repositories. Verify exit receipts, repeated close, and retained resources with actual process trees.
Run isolated macOS and Linux tests before claiming support for either host.

Then assess mutable worktrees, shared Git metadata, interactive Herdr detection, and native agent sandbox compatibility.
The [nono Codex guide](https://nono.sh/docs/cli/clients/codex) recommends changing Codex sandbox settings. Such changes require explicit review.
Preserve user approval controls. Add brokered tools, credential proxying, audit links, and rollback only through later scoped tasks.
Update setup instructions and the launch UML diagram when an integration changes the runtime contract.

## Current conservative limits

Close can retain branches, workspace/root shells, the caller, and active or uncertain resources.
It can retain dirty or unowned worktrees.
A oneshot Orchestrator without its own exit receipt remains retained.
Runtime output reports these limits.
Stronger cleanup requires a demonstrated requirement and bounded design.

Role permissions are cooperative.
External filesystem/Git changes and ABA races remain outside runtime locks.
Worktrees isolate paths. They do not establish semantic merge compatibility.

When you select an item, define its trigger, deliverable, ownership, acceptance evidence, and required external authorization.
Link the resulting task here. Keep unresolved items pending. Keep each bounded task within its selected scope.
