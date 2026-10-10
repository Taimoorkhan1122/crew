from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Resources(StrictModel):
    min_free_mem_gb: float = Field(ge=0)
    max_load_per_core: float = Field(gt=0)
    max_workers: int = Field(ge=1)
    min_quota_runway_seconds: float = Field(ge=0)


class Role(StrictModel):
    cli: str
    herdr_kind: str
    model: str
    effort: str
    args: list[str]
    mode: Literal["interactive", "oneshot"]
    readonly: bool
    prompt: str | None = None
    skills: list[str]
    axis: list[str]


class Handoff(StrictModel):
    in_: str = Field(alias="in")
    out: str


class Team(StrictModel):
    roles: list[str]
    handoffs: dict[str, Handoff]
    done_marker: str
    axis: list[str]


class Loop(StrictModel):
    max_fail_rounds: int = Field(ge=0)


class FlowStep(StrictModel):
    team: str
    gate: str | None = None
    loop: Loop | None = None
    decision: str | None = None
    action: Literal["push", "pr", "merge"] | None = None


class Flow(StrictModel):
    steps: list[FlowStep]
    max_fail_rounds: int = Field(ge=0)


class AxisTool(StrictModel):
    bin: str
    env: list[str]
    install: str | list[str] | None = None
    write_gate: str | None = None
    skill: str | None = None


class TestEnvironment(StrictModel):
    up: str
    down: str


class CrewConfig(StrictModel):
    herdr_session: str
    resources: Resources
    roles: dict[str, Role]
    teams: dict[str, Team]
    flows: dict[str, Flow]
    axis: dict[str, AxisTool]
    repos: list[str] | None = None
    base_branch: str | None = None
    worktree_pattern: str | None = None
    commands: dict[str, str] | None = None
    test_env: TestEnvironment | None = None
    tracker: dict[str, str] | None = None
    rules: list[str] | None = None


class UniqueSafeLoader(yaml.SafeLoader):
    """Reject duplicate explicit mapping keys before SafeLoader discards them."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = ('<<' if key_node.tag == 'tag:yaml.org,2002:merge'
                   else self.construct_object(key_node, deep=deep))
            try:
                if key in seen:
                    raise ValueError(f'duplicate YAML key at line {key_node.start_mark.line + 1}')
                seen.add(key)
            except TypeError as error:
                raise ValueError('YAML mapping key must be hashable') from error
        return super().construct_mapping(node, deep=deep)


def decode_yaml(text: str):
    """Decode safe YAML with duplicate-key rejection at every mapping depth."""
    try:
        return yaml.load(text, Loader=UniqueSafeLoader)
    except yaml.YAMLError as error:
        raise ValueError('invalid YAML syntax') from error


def _read_yaml(path: Path) -> dict:
    value = decode_yaml(path.read_text(encoding="utf-8"))
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    return value


def _merge(base: dict, override: Mapping) -> dict:
    result = dict(base)
    for key, value in override.items():
        if key == "handoffs" and isinstance(value, Mapping):
            result[key] = dict(value)
        elif isinstance(result.get(key), dict) and isinstance(value, Mapping):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_references(config: CrewConfig) -> None:
    for role_name, role in config.roles.items():
        for axis_name in role.axis:
            if axis_name not in config.axis:
                raise ValueError(f"role {role_name!r} references unknown AXI {axis_name!r}")
    for team_name, team in config.teams.items():
        for role_name in team.roles:
            if role_name not in config.roles:
                raise ValueError(f"team {team_name!r} references unknown role {role_name!r}")
        missing_handoffs = sorted(set(team.roles) - set(team.handoffs))
        unknown_handoffs = sorted(set(team.handoffs) - set(team.roles))
        if missing_handoffs or unknown_handoffs:
            raise ValueError(
                f"team {team_name!r} handoffs must match team roles "
                f"(missing: {missing_handoffs}; unknown: {unknown_handoffs})"
            )
        for axis_name in team.axis:
            if axis_name not in config.axis:
                raise ValueError(f"team {team_name!r} references unknown AXI {axis_name!r}")
    for flow_name, flow in config.flows.items():
        for step in flow.steps:
            if step.team not in config.teams:
                raise ValueError(f"flow {flow_name!r} references unknown team {step.team!r}")


def load_config(
    global_dir: str | Path,
    *,
    project_file: str | Path | None = None,
    run_file: str | Path | None = None,
) -> CrewConfig:
    """Load global YAML files, then recursively apply project and run overrides."""
    root = Path(global_dir)
    merged = _read_yaml(root / "config.yaml")
    for filename, key in (("roles.yaml", "roles"), ("teams.yaml", "teams"), ("axis.yaml", "axis")):
        merged[key] = _read_yaml(root / filename)

    flows: dict[str, dict] = {}
    flow_dir = root / "flows"
    for path in sorted((*flow_dir.glob("*.yaml"), *flow_dir.glob("*.yml"))):
        if path.stem in flows:
            raise ValueError(f"duplicate flow name: {path.stem}")
        flows[path.stem] = _read_yaml(path)
    merged["flows"] = flows

    for override_path in (project_file, run_file):
        if override_path is not None:
            merged = _merge(merged, _read_yaml(Path(override_path)))
    config = CrewConfig.model_validate(merged)
    _validate_references(config)
    return config
