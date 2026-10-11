"""Split project configuration: precedence, shapes, protections, and pinning."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from crew.config import load_config
from crew.runtime import Runtime, config_set, create_run, init_project, project_config


def _write_yaml(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _role(**overrides):
    role = {
        "cli": "claude", "herdr_kind": "claude", "model": "haiku", "effort": "low",
        "args": [], "mode": "interactive", "readonly": True, "skills": [], "axis": [],
    }
    role.update(overrides)
    return role


def _global(root: Path) -> Path:
    _write_yaml(root / "config.yaml", {
        "herdr_session": "crew-lab",
        "resources": {"min_free_mem_gb": 2, "max_load_per_core": 1.5,
                      "max_workers": 2, "min_quota_runway_seconds": 60},
    })
    _write_yaml(root / "roles.yaml", {
        "qa": _role(axis=["gh"]),
        "dev-worker": _role(model="sonnet", mode="oneshot", readonly=False),
    })
    _write_yaml(root / "teams.yaml", {
        "validator": {"roles": ["qa"], "handoffs": {"qa": {"in": "brief.md", "out": "result.md"}},
                      "done_marker": "QA DONE", "axis": ["gh"]},
        "dev": {"roles": ["dev-worker", "qa"],
                "handoffs": {"dev-worker": {"in": "result.md", "out": "pr.md"},
                             "qa": {"in": "brief.md", "out": "result.md"}},
                "done_marker": "DEV DONE", "axis": []},
    })
    _write_yaml(root / "axis.yaml", {"gh": {"bin": "gh-axi", "env": []}})
    _write_yaml(root / "flows" / "readonly.yaml", {"steps": [{"team": "validator"}], "max_fail_rounds": 0})
    return root


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


def _cli(setup, *args, role="lead"):
    return subprocess.run(
        [sys.executable, "-m", "crew.cli", "--global-dir", str(setup[0]), "--project", str(setup[1]), *args],
        capture_output=True, text=True, env={**os.environ, "CREW_ROLE": role, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_project_split_partial_override_and_precedence(tmp_path: Path) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    _write_yaml(project / ".crew" / "config.yaml", {"resources": {"max_workers": 5}})
    _write_yaml(project / ".crew" / "roles.yaml", {"qa": {"model": "split-model"}})
    _write_yaml(project / ".crew" / "project.yaml", {"resources": {"max_workers": 6}})

    split_config = load_config(global_dir, project_root=project,
                               project_file=project / ".crew" / "project.yaml")
    assert split_config.roles["qa"].model == "split-model"   # split overrides global
    assert split_config.roles["qa"].cli == "claude"          # untouched global field inherited
    assert split_config.roles["qa"].effort == "low"
    assert split_config.resources.max_workers == 6           # project.yaml wins over split

    run_file = tmp_path / "run.yaml"
    _write_yaml(run_file, {"resources": {"max_workers": 7}, "roles": {"qa": {"effort": "high"}}})
    run_config = load_config(global_dir, project_root=project,
                             project_file=project / ".crew" / "project.yaml", run_file=run_file)
    assert run_config.resources.max_workers == 7             # explicit run override wins
    assert run_config.roles["qa"].effort == "high"


def test_missing_project_split_inherits_global(tmp_path: Path) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)

    config = load_config(global_dir, project_root=project)

    assert config.roles["qa"].model == "haiku"
    assert config.resources.max_workers == 2
    assert config.flows["readonly"].steps[0].team == "validator"


def test_lists_replace_and_handoffs_mapping_replaces_whole(tmp_path: Path) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    _write_yaml(project / ".crew" / "roles.yaml", {"qa": {"axis": []}})
    _write_yaml(project / ".crew" / "teams.yaml", {"dev": {
        "roles": ["dev-worker"],
        "handoffs": {"dev-worker": {"in": "p/{ticket}.md", "out": "r/{ticket}.md"}},
    }})

    config = load_config(global_dir, project_root=project)

    assert config.roles["qa"].axis == []                     # list replaced, not merged
    assert config.teams["dev"].roles == ["dev-worker"]       # list replaced, not merged
    assert set(config.teams["dev"].handoffs) == {"dev-worker"}   # whole mapping replaced
    assert config.teams["dev"].handoffs["dev-worker"].in_ == "p/{ticket}.md"


def test_project_split_new_entities_and_yml_flow_override(tmp_path: Path) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    _write_yaml(project / ".crew" / "axis.yaml", {"new-axi": {"bin": "new-axi", "env": []}})
    _write_yaml(project / ".crew" / "roles.yaml", {"worker": _role(
        cli="opencode", herdr_kind="opencode", model="free/model", mode="oneshot",
        readonly=False, axis=["new-axi"])})
    _write_yaml(project / ".crew" / "teams.yaml", {"builder": {
        "roles": ["worker"], "handoffs": {"worker": {"in": "a.md", "out": "b.md"}},
        "done_marker": "BUILD DONE", "axis": []}})
    _write_yaml(project / ".crew" / "flows" / "extra.yaml",
                {"steps": [{"team": "builder"}], "max_fail_rounds": 0})
    _write_yaml(project / ".crew" / "flows" / "readonly.yml",
                {"steps": [{"team": "builder"}], "max_fail_rounds": 1})

    config = load_config(global_dir, project_root=project)

    assert config.roles["worker"].axis == ["new-axi"]        # satisfied by project axis layer
    assert config.flows["extra"].steps[0].team == "builder"
    assert config.flows["readonly"].steps[0].team == "builder"   # .yml overrides global .yaml
    assert config.flows["readonly"].max_fail_rounds == 1


@pytest.mark.parametrize(("relative", "text", "match"), [
    ("roles.yaml", "qa: [\n", "invalid YAML syntax"),
    ("config.yaml", "herdr_session: a\nherdr_session: b\n", "duplicate YAML key"),
    ("roles.yaml", "- a\n- b\n", "expected a YAML mapping"),
])
def test_project_split_invalid_input_rejected(tmp_path: Path, relative: str, text: str, match: str) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    _write_text(project / ".crew" / relative, text)

    with pytest.raises(ValueError, match=match):
        load_config(global_dir, project_root=project)


def test_project_split_unknown_field_rejected(tmp_path: Path) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    _write_yaml(project / ".crew" / "roles.yaml", {"qa": {"model_default": "haiku"}})

    with pytest.raises(ValidationError, match="model_default"):
        load_config(global_dir, project_root=project)


def test_project_split_unknown_reference_rejected(tmp_path: Path) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    _write_yaml(project / ".crew" / "roles.yaml", {"qa": {"axis": ["missing-axi"]}})

    with pytest.raises(ValueError, match="unknown AXI"):
        load_config(global_dir, project_root=project)


@pytest.mark.parametrize(("text", "match"), [
    ("- a\n- b\n", "expected a YAML mapping"),
    ("steps: []\nsteps: []\n", "duplicate YAML key"),
])
def test_project_split_invalid_flow_input_rejected(tmp_path: Path, text: str, match: str) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    _write_text(project / ".crew" / "flows" / "bad.yaml", text)

    with pytest.raises(ValueError, match=match):
        load_config(global_dir, project_root=project)


def test_project_split_unknown_flow_reference_rejected(tmp_path: Path) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    _write_yaml(project / ".crew" / "flows" / "extra.yaml",
                {"steps": [{"team": "ghost"}], "max_fail_rounds": 0})

    with pytest.raises(ValueError, match="unknown team"):
        load_config(global_dir, project_root=project)


def test_project_split_duplicate_flow_name_within_layer_rejected(tmp_path: Path) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    _write_yaml(project / ".crew" / "flows" / "dup.yaml", {"steps": [{"team": "validator"}], "max_fail_rounds": 0})
    _write_yaml(project / ".crew" / "flows" / "dup.yml", {"steps": [{"team": "validator"}], "max_fail_rounds": 0})

    with pytest.raises(ValueError, match="duplicate flow name"):
        load_config(global_dir, project_root=project)


@pytest.mark.parametrize("kind", ["crew_dir", "roles_file", "flows_dir", "flow_file"])
def test_project_split_symlink_rejected(tmp_path: Path, kind: str) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "roles.yaml").write_text("qa: {}\n")
    (outside / "readonly.yaml").write_text("steps: []\n")
    crew = project / ".crew"
    if kind == "crew_dir":
        crew.symlink_to(outside)
    elif kind == "roles_file":
        crew.mkdir()
        (crew / "roles.yaml").symlink_to(outside / "roles.yaml")
    elif kind == "flows_dir":
        crew.mkdir()
        (crew / "flows").symlink_to(outside)
    else:
        (crew / "flows").mkdir(parents=True)
        (crew / "flows" / "readonly.yaml").symlink_to(outside / "readonly.yaml")

    with pytest.raises(ValueError, match="symlink"):
        project_config(global_dir, project)


def test_config_set_validates_actual_split_context(tmp_path: Path) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    _write_yaml(project / ".crew" / "roles.yaml", {"worker": _role(
        cli="opencode", herdr_kind="opencode", model="orig", mode="oneshot", readonly=False)})

    # The candidate supplies only worker.model; validity needs the split role definition.
    result = config_set(global_dir, project, "roles.worker.model=claude/x", scope="project")

    assert result["status"] == "updated"
    config = project_config(global_dir, project)
    assert config.roles["worker"].model == "claude/x"
    assert config.roles["worker"].cli == "opencode"          # inherited from split role


def test_config_set_rejects_invalid_and_preserves_files(tmp_path: Path) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    split = project / ".crew" / "roles.yaml"
    _write_yaml(split, {"worker": _role(
        cli="opencode", herdr_kind="opencode", model="orig", mode="oneshot", readonly=False)})
    before = split.read_bytes()

    with pytest.raises(ValueError, match="unknown AXI"):
        config_set(global_dir, project, 'roles.worker.axis=["nope"]', scope="project")

    assert split.read_bytes() == before
    assert not (project / ".crew" / "project.yaml").exists()


def test_config_set_repairs_split_reference_conflict(tmp_path: Path) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    _write_yaml(project / ".crew" / "roles.yaml", {"qa": {"axis": ["missing-axi"]}})
    with pytest.raises(ValueError, match="unknown AXI"):
        project_config(global_dir, project)

    result = config_set(global_dir, project, 'roles.qa.axis=["gh"]', scope="project")

    assert result["status"] == "updated"
    assert project_config(global_dir, project).roles["qa"].axis == ["gh"]


def test_cli_show_and_run_freeze_project_split_without_launch(tmp_path: Path) -> None:
    global_dir = _global(tmp_path / "global")
    project = tmp_path / "project"
    _write_yaml(project / ".crew" / "roles.yaml", {"qa": {"model": "claude/haiku"}})
    setup = (global_dir, project)

    show = _cli(setup, "config", "show")
    assert show.returncode == 0, show.stdout
    assert "claude/haiku" in show.stdout                    # real CLI reads the split override

    validate = _cli(setup, "config", "validate")
    assert validate.returncode == 0, validate.stdout

    created = _cli(setup, "run", "new", "--id", "one", "--flow", "readonly", "--items", "A")
    assert created.returncode == 0, created.stdout
    run_yaml = project / ".crew" / "runs" / "one" / "run.yaml"
    frozen = run_yaml.read_bytes()
    assert yaml.safe_load(frozen)["roles"]["qa"]["model"] == "claude/haiku"

    # Project and global edits after the freeze must not change the existing run.
    _write_yaml(project / ".crew" / "roles.yaml", {"qa": {"model": "claude/other"}})
    _write_yaml(global_dir / "config.yaml", {
        "herdr_session": "crew-lab",
        "resources": {"min_free_mem_gb": 2, "max_load_per_core": 1.5,
                      "max_workers": 4, "min_quota_runway_seconds": 60},
    })
    assert run_yaml.read_bytes() == frozen
    Runtime(project / ".crew" / "runs" / "one").check_config()

    old_show = _cli(setup, "config", "show", "--run", "one")
    assert old_show.returncode == 0, old_show.stdout
    assert "claude/haiku" in old_show.stdout
    assert "claude/other" not in old_show.stdout

    status = _cli(setup, "status", "one")
    assert status.returncode == 0, status.stdout

    created2 = _cli(setup, "run", "new", "--id", "two", "--flow", "readonly", "--items", "B")
    assert created2.returncode == 0, created2.stdout
    new_frozen = yaml.safe_load((project / ".crew" / "runs" / "two" / "run.yaml").read_text())
    assert new_frozen["roles"]["qa"]["model"] == "claude/other"
    assert new_frozen["resources"]["max_workers"] == 4


def test_init_preserves_existing_and_activates_copied_defaults(tmp_path: Path) -> None:
    global_dir = _global(tmp_path / "global")
    project = _project(tmp_path)
    crew = project / ".crew"
    crew.mkdir()
    (crew / "project.yaml").write_text("rules: [preserve]\n")
    (crew / "prompts").mkdir()
    (crew / "prompts" / "custom.md").write_text("custom")
    (crew / "roles.yaml").write_text("qa:\n  model: pinned-model\n")

    init_project(global_dir, project)
    init_project(global_dir, project)

    assert (crew / "project.yaml").read_text() == "rules: [preserve]\n"
    assert (crew / "prompts" / "custom.md").read_text() == "custom"
    assert (crew / "roles.yaml").read_text() == "qa:\n  model: pinned-model\n"
    assert (crew / "teams.yaml").exists()                   # copied default is now present

    config = project_config(global_dir, project)
    assert config.roles["qa"].model == "pinned-model"       # preserved split is active
    assert config.roles["qa"].cli == "claude"               # untouched global fields inherited
    assert config.flows["readonly"].steps[0].team == "validator"

    # Later global changes are pinned by the copied project values.
    _write_yaml(global_dir / "roles.yaml", {
        "qa": _role(model="global-changed", axis=["gh"]),
        "dev-worker": _role(model="sonnet", mode="oneshot", readonly=False),
    })
    _write_yaml(global_dir / "config.yaml", {
        "herdr_session": "crew-lab",
        "resources": {"min_free_mem_gb": 2, "max_load_per_core": 1.5,
                      "max_workers": 9, "min_quota_runway_seconds": 60},
    })

    pinned = project_config(global_dir, project)
    assert pinned.roles["qa"].model == "pinned-model"
    assert pinned.resources.max_workers == 2

    path = create_run(global_dir, project, "pinrun", "readonly", ["A"])
    frozen = yaml.safe_load((path / "run.yaml").read_text())
    assert frozen["roles"]["qa"]["model"] == "pinned-model"
    assert frozen["resources"]["max_workers"] == 2
