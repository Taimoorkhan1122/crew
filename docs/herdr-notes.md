# Herdr smoke findings

Date: 2026-10-08. This public summary describes a historical test in an isolated named session.
Private paths, execution identifiers, and internal migration references are excluded.

## Result

Claude Haiku and OpenCode interactive prompts returned their expected markers. OpenCode oneshot execution returned its expected marker.
The test used Herdr 0.9.3, protocol 22. OpenCode displayed the Big Pickle model.
Only resources created for the test were closed. The test left both existing and isolated Herdr servers running.
These statements describe the test result. They do not describe current server status.

## Session isolation

The first workspace creation failed because the isolated server was absent.
The first server start failed with a permission error. A later start succeeded with user authorization.
All workspace and pane operations selected the isolated session explicitly. Resource creation preserved focus.
Final pane and workspace listings for the test session were empty.

## Agent behavior

Claude first required a folder-trust choice. The user authorized that choice before the test continued.
Claude became ready and returned `CREW_CLAUDE_OK`. Herdr reported `idle` after the completed turn.
The agent output included its completed-turn marker. No tool call occurred.

OpenCode became ready and returned `CREW_OPEN_CODE_OK`. Herdr reported `done` after the prompt.
Model-list commands returned no model IDs. This result did not establish model unavailability.
The test selected `opencode/big-pickle` explicitly.

OpenCode oneshot execution returned `CREW_ONESHOT_OK` in agent output.
The initial output match found an echoed command. The reviewer excluded that match and inspected the response directly.

## Limits

This test established direct Herdr agent interaction in the tested environment.
It did not establish Crew runtime dispatch, process cleanup, account E2E, or support for other hosts.
No account data or secrets were read or copied. No live QA environment was created.
See the [acceptance record](release-acceptance.md) for the later reduced release and its remaining limits.
