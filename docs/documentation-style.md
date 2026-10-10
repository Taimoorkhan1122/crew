# Documentation style

Use ASD-STE100, Issue 9, for current project documentation. Apply this policy to the README, guides, project instructions, and diagram captions.

## Writing rules

1. Use the approved dictionary meanings and parts of speech. Use defined technical terms where the dictionary does not cover software concepts.
2. Use one name for each concept. Keep technical noun groups short. Explain long names before you use a shorter form.
3. Use direct commands in procedures. Use active voice in descriptions when the actor is known.
4. Write one instruction in each sentence. Put a necessary condition before the instruction.
5. Use a maximum of 20 words in an instruction sentence. Use a maximum of 25 words in a descriptive sentence.
6. Keep each sentence about one topic. Use short paragraphs. Use lists for steps or related items.
7. Use American English spelling. Avoid contractions and figurative language.

These rules summarize selected requirements. Use the [official standard](https://www.asd-ste100.org/assets/files/ASD-STE100_ISSUE9.pdf) for the complete rules and dictionary.
The [official FAQ](https://www.asd-ste100.org/faq.html) explains vocabulary and technical terms.

## Project terms

The table defines software terms for this project. Exact Python names, YAML keys, CLI commands, and product names keep their source spelling.

| Term | Use | Meaning |
| --- | --- | --- |
| Lead | Noun | Role that presents user decisions and supervises work. |
| Orchestrator | Noun | Role that calls Crew to advance a run. |
| worker | Noun | Agent that does an assigned task. |
| run | Noun | Crew execution record with configuration, tasks, decisions, and resource records. |
| task | Noun | Assignment for one role at one flow step and round. |
| flow | Noun | Ordered steps, gates, and possible repair rounds. |
| gate | Noun | Condition that controls progress to a flow step. |
| artifact | Noun | Assigned output file with task evidence. |
| exit receipt | Noun | Record that links a process exit code to its launch intent. |
| launch intent | Noun | Saved identifier for one attempt to start a process. |
| snapshot | Noun | Copy of state or source content at a specified time. |
| fingerprint | Noun | Saved identity and content data for an artifact. |
| overlay | Noun | Configuration values that replace values from an earlier source. |
| frozen configuration | Noun | Saved run configuration that runtime protects from changes after start. |
| lane | Noun | Item group with assigned repository worktrees. |
| worktree | Noun | Git working directory with a separate branch. |
| oneshot launch | Noun | Launch that executes one command and records its exit. |
| oneshot task | Noun | Task whose role uses the `oneshot` launch mode. |
| readonly role | Noun | Role that permits inspection and the assigned report. |
| readonly flow | Noun | Example flow that assigns work to a readonly role. |
| reconciliation | Noun | Comparison of current evidence with saved ownership and launch records. |
| CLI | Noun | Command-line interface. |
| IO | Noun | Input/output operations. |
| E2E | Noun | Verification through the complete execution path. |
| ABA | Noun | A change that returns a value to its earlier content. |
| TOON | Noun | Text format for Crew CLI output. |
| dispatch | Verb | Reserve and request the launch of a configured worker. |
| reconcile | Verb | Compare current evidence with saved ownership and launch records. |
| validate | Verb | Compare software data with its required schema or rules. |
| freeze | Verb | Save validated values as a run configuration. |

Use technical nouns as nouns. Use technical verbs as verbs. Add a definition when a new software concept needs a technical term.
Values such as `oneshot` and `readonly` keep their exact spelling in configuration examples.

## Exact text and historical evidence

Preserve executable syntax, API signatures, keys, error text, and completion markers. Use generic paths and identifiers in public examples.
Keep the six UML diagrams accurate. Follow the [diagram instructions](../AGENTS.md#uml-documentation) when a contract changes.

Preserve historical results and limits. Public [acceptance summaries](release-acceptance.md) and [Herdr smoke findings](herdr-notes.md) exclude private execution details.
Keep original reports outside the public repository. Follow the [publication rules](../AGENTS.md#public-documentation).

## Review

1. Compare the revised text with the source behavior.
2. Do a check of sentence length, instruction order, terminology, and parts of speech.
3. Validate links, command arguments, and diagram syntax.
4. Compare example behavior and historical results with their earlier versions. Exclude private details from public copies.
5. Record checks and remaining vocabulary review work in the handoff.

A sentence-length check does not prove full STE compliance. Full compliance also requires dictionary, meaning, grammar, and technical-term review.
The current revision includes structural checks and a manual language review. It has no complete dictionary audit.
