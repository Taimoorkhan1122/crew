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


def safe_child(root, relative):
    """Reject absolute paths, traversal and symlinks in every existing component."""
    root = Path(root)
    relative = Path(relative)
    if relative.is_absolute() or not relative.parts or any(p in ('..', '.') for p in relative.parts):
        raise ValueError('relative in-root path required')
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('symlink paths are forbidden')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('path escapes root')
    return path


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


_SECTION_FILES = (("roles.yaml", "roles"), ("teams.yaml", "teams"), ("axis.yaml", "axis"))


def _flows_in(root: Path, *, guard: bool = False) -> dict[str, dict]:
    """Read each flow file. Reject duplicate stems. Guard each path when requested."""
    flows: dict[str, dict] = {}
    for path in sorted((*root.glob("*.yaml"), *root.glob("*.yml"))):
        if guard:
            safe_child(root, path.name)
        if path.stem in flows:
            raise ValueError(f"duplicate flow name: {path.stem}")
        flows[path.stem] = _read_yaml(path)
    return flows


def _global_layer(root: Path) -> dict:
    merged = _read_yaml(root / "config.yaml")
    for filename, key in _SECTION_FILES:
        merged[key] = _read_yaml(root / filename)
    merged["flows"] = _flows_in(root / "flows")
    return merged


def _project_layer(project_root: Path) -> dict:
    """Read project split files, checking the directory and each consumed file."""
    crew = safe_child(Path(project_root), ".crew")
    layer: dict = {}
    config_path = safe_child(crew, "config.yaml")
    if config_path.exists():
        layer = _read_yaml(config_path)
    for filename, key in _SECTION_FILES:
        path = safe_child(crew, filename)
        if path.exists():
            layer[key] = _read_yaml(path)
    flow_dir = safe_child(crew, "flows")
    if flow_dir.is_dir():
        layer["flows"] = _flows_in(flow_dir, guard=True)
    return layer


def load_config(
    global_dir: str | Path,
    *,
    project_root: str | Path | None = None,
    project_file: str | Path | None = None,
    run_file: str | Path | None = None,
) -> CrewConfig:
    """Load global files, project split files, project.yaml, then run overrides."""
    root = Path(global_dir)
    merged = _global_layer(root)
    if project_root is not None:
        merged = _merge(merged, _project_layer(Path(project_root)))
    for override_path in (project_file, run_file):
        if override_path is not None:
            merged = _merge(merged, _read_yaml(Path(override_path)))
    config = CrewConfig.model_validate(merged)
    _validate_references(config)
    return config
