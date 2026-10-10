from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from crew.config import load_config


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _global_config(root: Path) -> Path:
    _write_yaml(root / "config.yaml", {
        "herdr_session": "crew-lab",
        "resources": {
            "min_free_mem_gb": 2,
            "max_load_per_core": 1.5,
            "max_workers": 2,
            "min_quota_runway_seconds": 60,
        },
    })
    _write_yaml(root / "roles.yaml", {"qa": {
        "cli": "claude", "herdr_kind": "claude", "model": "haiku", "effort": "low",
        "args": [], "mode": "interactive", "readonly": True, "skills": [], "axis": [],
    }})
    _write_yaml(root / "teams.yaml", {"validator": {
        "roles": ["qa"], "handoffs": {"qa": {"in": "brief.md", "out": "result.md"}},
        "done_marker": "QA DONE", "axis": [],
    }})
    _write_yaml(root / "axis.yaml", {"gh": {"bin": "gh-axi", "env": []}})
    _write_yaml(root / "flows" / "readonly.yaml", {
        "steps": [{"team": "validator"}], "max_fail_rounds": 1,
    })
    return root


def test_load_config_merges_global_project_and_run(tmp_path: Path) -> None:
    global_dir = _global_config(tmp_path / "global")
    project_file = tmp_path / "project.yaml"
    _write_yaml(project_file, {"resources": {"max_workers": 4}, "roles": {"qa": {"model": "claude/haiku"}}})
    run_file = tmp_path / "run.yaml"
    _write_yaml(run_file, {"roles": {"qa": {"effort": "high"}}})

    config = load_config(global_dir, project_file=project_file, run_file=run_file)

    assert config.resources.max_workers == 4
    assert config.roles["qa"].model == "claude/haiku"
    assert config.roles["qa"].effort == "high"


def test_config_rejects_unknown_role_fields(tmp_path: Path) -> None:
    global_dir = _global_config(tmp_path / "global")
    roles = yaml.safe_load((global_dir / "roles.yaml").read_text())
    roles["qa"]["model_default"] = "haiku"
    _write_yaml(global_dir / "roles.yaml", roles)

    with pytest.raises(ValidationError, match="model_default"):
        load_config(global_dir)


@pytest.mark.parametrize("required_field", ["model", "effort"])
def test_config_requires_role_model_and_effort(tmp_path: Path, required_field: str) -> None:
    global_dir = _global_config(tmp_path / "global")
    roles = yaml.safe_load((global_dir / "roles.yaml").read_text())
    del roles["qa"][required_field]
    _write_yaml(global_dir / "roles.yaml", roles)

    with pytest.raises(ValidationError, match=required_field):
        load_config(global_dir)


@pytest.mark.parametrize(("section", "change", "message"), [
    ("roles", lambda data: data["qa"].update(axis=["missing"]), "role 'qa'.*AXI 'missing'"),
    ("teams", lambda data: data["validator"].update(roles=["missing"]), "team 'validator'.*role 'missing'"),
    ("teams", lambda data: data["validator"].update(axis=["missing"]), "team 'validator'.*AXI 'missing'"),
    ("flows", lambda data: data["steps"][0].update(team="missing"), "flow 'readonly'.*team 'missing'"),
])
def test_config_rejects_dangling_role_team_and_axis_references(
    tmp_path: Path, section: str, change, message: str,
) -> None:
    global_dir = _global_config(tmp_path / "global")
    path = global_dir / ("flows/readonly.yaml" if section == "flows" else f"{section}.yaml")
    data = yaml.safe_load(path.read_text())
    change(data)
    _write_yaml(path, data)

    with pytest.raises(ValueError, match=message):
        load_config(global_dir)


def test_config_validates_references_after_project_overrides(tmp_path: Path) -> None:
    global_dir = _global_config(tmp_path / "global")
    project_file = tmp_path / "project.yaml"
    _write_yaml(project_file, {
        "roles": {"worker": {
            "cli": "opencode", "herdr_kind": "opencode", "model": "free/model",
            "effort": "low", "args": [], "mode": "oneshot", "readonly": True,
            "skills": [], "axis": ["gh"],
        }},
        "teams": {"validator": {
            "roles": ["worker"],
            "handoffs": {"worker": {"in": "brief.md", "out": "result.md"}},
        }},
    })

    config = load_config(global_dir, project_file=project_file)

    assert config.teams["validator"].roles == ["worker"]


@pytest.mark.parametrize(
    ("handoffs", "message"),
    [
        ({"typo": {"in": "brief.md", "out": "result.md"}}, "missing: \\[.*qa.*\\].*unknown: \\[.*typo.*\\]"),
        ({}, "missing: \\[.*qa.*\\]"),
    ],
)
def test_config_requires_handoff_paths_for_exactly_the_active_team_roles(
    tmp_path: Path, handoffs: dict, message: str,
) -> None:
    global_dir = _global_config(tmp_path / "global")
    teams = yaml.safe_load((global_dir / "teams.yaml").read_text())
    teams["validator"]["handoffs"] = handoffs
    _write_yaml(global_dir / "teams.yaml", teams)

    with pytest.raises(ValueError, match=message):
        load_config(global_dir)


@pytest.mark.parametrize('location', ['root','nested','project','run'])
def test_loader_rejects_duplicate_yaml_keys_before_validation(tmp_path,location):
    root=_global_config(tmp_path/'global')
    kwargs={}
    if location=='root':
        path=root/'config.yaml'; text=path.read_text()+'herdr_session: crew-lab\n'
    elif location=='nested':
        path=root/'config.yaml'; text=path.read_text().replace('  max_workers: 2','  max_workers: 2\n  max_workers: 2')
    else:
        path=tmp_path/(location+'.yaml')
        text='resources:\n  max_workers: 1\nresources:\n  max_workers: 4\n'
        kwargs[location+'_file']=path
    path.write_text(text)
    with pytest.raises(ValueError,match='duplicate YAML key'): load_config(root,**kwargs)
