# Crew agent skill

The Crew agent skill teaches an agent to set up, configure, and supervise Crew runs.
The skill is portable. It is one folder with `SKILL.md`, references, and agent metadata.

Source folder: `skills/crew/`.
The folder is self-contained. A copy works in another agent after you copy the whole folder.
The skill folder ships in the source checkout. A wheel installation does not include `skills/crew/`.

## Install the skill

Copy the complete `skills/crew/` folder into the skill directory of your agent.
Keep the folder name `crew`.

| Agent | Typical location |
| --- | --- |
| Codex | `~/.codex/skills/crew` |
| Claude Code | `~/.claude/skills/crew` |
| OpenCode | Use the OpenCode skill discovery and help. This document does not claim a location. |

If a `crew` skill already exists, stop.
Choose an explicit action: back up the existing skill, or update it.
Do not overwrite an existing skill without a user choice.

Example copy command:

```sh
skill_src="skills/crew"
skill_dest="$HOME/.codex/skills/crew"

if [ -e "$skill_dest" ] || [ -L "$skill_dest" ]; then
  echo "Existing path: $skill_dest"
  echo "Stop. Back up or update the existing skill explicitly."
else
  mkdir -p "$(dirname "$skill_dest")"
  cp -R "$skill_src" "$skill_dest"
fi
```

The test covers an existing directory and a symlink. `-L` also covers a dangling symlink.
The copy runs only into an absent destination. This action prevents a nested copy.

## Invoke the skill

Use `$crew` where the agent supports a skill name.
If the agent does not support `$crew`, the agent reads `SKILL.md` in the installed folder.
Then read the applicable reference.

## Relationship to Crew

Crew role `skills` values are configuration only.
Runtime and launch code do not install, load, or enforce a skill.
The Crew skill does not install itself from a role.
This document describes a manual copy. It does not install the skill.

## Related documents

- [Setup](setup.md)
- [Getting started](quickstart.md)
- [Configuration and migration](config-migration.md)
- [Runtime and CLI](runtime-cli.md)
- [Agent compatibility](agent-compatibility.md)
- [Lead and Orchestrator workflow](lead-orchestrator-workflow.md)
- [Development](development.md)
- [Configuration model diagram](config-migration.md#configuration-model)
- [Task lifecycle diagram](runtime-cli.md#task-lifecycle)
