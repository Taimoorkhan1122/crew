# Backlog

Updated 2026-10-10. The five-item [reduced release](remaining-scope.md) is accepted.
These items remain deferred. They do not authorize execution or block the accepted scope.

## Validation and migration candidates

| Item | Current gap | Revisit condition |
| --- | --- | --- |
| Live Herdr acceptance | Tests passed. Live Crew launch and process cleanup remain unverified. | User authorizes an isolated session, topology changes, and cleanup. |
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
| Plugin files/hooks, marketplace, and topology/server changes | A real run needs separately authorized changes. |
| Environment up/down automation | An authorized verification task needs an environment. |
| Push/PR/merge and tool-level write enforcement | External actions have explicit scope and authorization. Current action roles remain refused. |
| AXI home-view capture and automatic installation | Onboarding needs these features. Installation requires approval. |
| Session adoption, quota watcher, and restore automation | Exact identity checks and explicit retries are insufficient. |
| Config/state transactions and editor coordination | Concurrent editing needs stronger guarantees. |

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
