"""End to end on the synthetic demo, and a guard that the committed sample plans are current."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import run_demo

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def generated(tmp_path_factory, demo):
    out = tmp_path_factory.mktemp("plans")
    names = run_demo.run(demo.repo, demo.changes, out)
    return out, names


def test_every_demo_change_gets_a_clean_plan(generated):
    out, names = generated
    assert sorted(names) == ["discount-cap", "refund-endpoint", "reservation-timeout"]
    for name in names:
        plan = json.loads((out / f"{name}.json").read_text())
        errors = [i for i in plan["validation"] if i["severity"] == "error"]
        assert errors == [], name


def test_every_acceptance_criterion_has_a_condition(generated, demo_bundles):
    out, names = generated
    for name in names:
        plan = json.loads((out / f"{name}.json").read_text())
        covered = {c["requirement"] for c in plan["cases"]}
        wanted = {r.id for r in demo_bundles[name].criteria()}
        assert wanted <= covered, name


def test_every_plan_has_conditions_at_the_top_priority(generated):
    out, names = generated
    for name in names:
        plan = json.loads((out / f"{name}.json").read_text())
        assert any(c["priority"] == "P0" for c in plan["cases"]), name


def test_generation_is_reproducible(generated, demo, tmp_path):
    out, names = generated
    again = tmp_path / "again"
    run_demo.run(demo.repo, demo.changes, again)
    for name in names:
        assert (out / f"{name}.md").read_text() == (again / f"{name}.md").read_text()
        assert (out / f"{name}.json").read_text() == (again / f"{name}.json").read_text()


def test_the_committed_sample_plans_are_up_to_date(generated):
    """If this fails, run ``python demo/run_demo.py`` and commit the result."""
    out, names = generated
    for name in names:
        for ext in ("md", "json"):
            committed = ROOT / "demo" / "plans" / f"{name}.{ext}"
            assert committed.read_text() == (out / f"{name}.{ext}").read_text(), committed.name


def test_the_demo_secret_is_not_in_any_plan_or_saved_context(generated):
    out, names = generated
    for name in names:
        assert "hunter2" not in (out / f"{name}.md").read_text()
        assert "hunter2" not in (out / f"{name}.json").read_text()


def test_the_demo_repo_refuses_to_delete_a_directory_it_did_not_create(tmp_path):
    (tmp_path / "precious.txt").write_text("keep me")
    with pytest.raises(SystemExit):
        run_demo.build_demo_repo.build(tmp_path, tmp_path / "changes")
    assert (tmp_path / "precious.txt").exists()


def test_the_package_runs_as_a_module():
    proc = subprocess.run(
        [sys.executable, "-m", "testplan_agent", "--version"],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""},
    )
    assert proc.returncode == 0 and "testplan" in proc.stdout
