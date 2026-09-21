"""Shared fixtures. Nothing here touches the network; git is used only for the history tests."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import build_demo_repo
import pytest

from testplan_agent.bundle import ContextBundle, build_bundle
from testplan_agent.llm import HeuristicClient
from testplan_agent.planner import generate_plan

AS_OF = "2026-09-01"
HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git is not installed")


@pytest.fixture(scope="session")
def demo(tmp_path_factory) -> SimpleNamespace:
    """The synthetic shop repository and its three changes, built once per test run."""
    if not HAS_GIT:
        pytest.skip("git is not installed")
    root = tmp_path_factory.mktemp("demo")
    repo, changes = root / "repo", root / "changes"
    names = build_demo_repo.build(repo, changes)
    return SimpleNamespace(root=root, repo=repo, changes=changes, names=names)


def read_change(demo: SimpleNamespace, name: str):
    folder = demo.changes / name
    return (folder / "change.patch").read_text(), (folder / "story.md").read_text()


@pytest.fixture(scope="session")
def demo_bundles(demo):
    from datetime import date

    out = {}
    for name in demo.names:
        diff, story = read_change(demo, name)
        out[name] = build_bundle(diff, story, repo=demo.repo, as_of=date.fromisoformat(AS_OF))
    return out


@pytest.fixture
def discount(demo_bundles, demo):
    """(bundle, valid plan as a dict, repo) for the discount-cap change."""
    bundle = demo_bundles["discount-cap"]
    result = generate_plan(bundle, HeuristicClient(), repo=demo.repo)
    assert result.ok, [str(i) for i in result.issues]
    return SimpleNamespace(
        bundle=copy.deepcopy(bundle), plan=copy.deepcopy(result.plan.to_dict()), repo=demo.repo
    )


def git(repo: Path, *args: str, date: str = "2026-08-01T12:00:00") -> None:
    env = {
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
        "PATH": __import__("os").environ.get("PATH", ""),
        "HOME": str(repo),
    }
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def write_files(root: Path, files: dict) -> None:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def dumps(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True)


__all__ = ["ContextBundle"]
