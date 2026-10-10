# Crew project instructions

Develop in this repository. Read [development](docs/development.md) before you change the implementation. Read [release scope](docs/remaining-scope.md) before you select work.

## Documentation language

Use ASD-STE100, Issue 9, for project documentation. Apply the [writing rules](docs/documentation-style.md) to new and changed text. Use the project term list for software terms. Preserve command behavior, public identifiers, markers, and historical results. Record vocabulary review gaps before you claim full STE compliance.

## UML documentation

- Use Mermaid UML diagrams to explain architecture, configuration, task states, and role interactions.
- Keep the README diagram small. Put detailed diagrams beside the applicable contract. Use links to existing diagrams.
- Use class diagrams for structure. Use sequence diagrams for interactions. Use state diagrams for task states.
- When you change a documented contract, update its diagram in the same change. Add a diagram when it helps explain the contract.
- Use current source as the diagram reference. Identify modules and Python classes. Separate task status from process status and cleanup status.
- Show conditions that can prevent progress. Identify details that the diagram does not show. Identify deferred work.
- Validate Mermaid syntax and document links. If a local renderer is available, render the diagrams. Report limits of the rendering checks.
- Preserve historical results and limits. Keep original reports outside the public repository when they contain private details.

Current diagrams: [overview](README.md#operating-model), [runtime structure](docs/development.md#runtime-structure), [role sequence](docs/lead-orchestrator-workflow.md#run-and-decision-sequence), [task lifecycle](docs/runtime-cli.md#task-lifecycle), and [configuration model](docs/config-migration.md#configuration-model).

## Public documentation

Use repository-relative paths or generic placeholders in public examples. Keep personal paths and internal project references outside this repository.
Exclude private session IDs, pane IDs, issue IDs, and evidence locations from public reports.
Before publication, inspect document links and Git history for private references. Label public evidence summaries and preserve their results and limits.
