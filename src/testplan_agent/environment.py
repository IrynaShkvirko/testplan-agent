"""Test environment: framework, CI, fixtures and data the plan should mention."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List

from . import codeinfo


def detect(repo: Path) -> Dict[str, Any]:
    env: Dict[str, Any] = {"frameworks": [], "ci": [], "fixtures": [], "test_data_dirs": []}
    frameworks: List[str] = env["frameworks"]

    conftests = codeinfo.iter_files(repo, "conftest.py")
    pyproject = codeinfo.read_text(repo / "pyproject.toml") or ""
    tox = codeinfo.read_text(repo / "tox.ini") or ""
    setup_cfg = codeinfo.read_text(repo / "setup.cfg") or ""
    if (
        (repo / "pytest.ini").exists()
        or "[tool.pytest" in pyproject
        or "[pytest]" in tox
        or "[tool:pytest]" in setup_cfg
        or bool(conftests)
    ):
        frameworks.append("pytest")

    package = codeinfo.read_text(repo / "package.json")
    if package:
        try:
            deps = {
                **json.loads(package).get("dependencies", {}),
                **json.loads(package).get("devDependencies", {}),
            }
        except (ValueError, AttributeError):
            deps = {}
        for name in ("jest", "vitest", "mocha", "playwright", "cypress"):
            if name in deps:
                frameworks.append(name)

    workflows = repo / ".github" / "workflows"
    if workflows.is_dir():
        env["ci"].extend(f".github/workflows/{p.name}" for p in sorted(workflows.glob("*.y*ml")))
    for name in (".gitlab-ci.yml", "Jenkinsfile"):
        if (repo / name).exists():
            env["ci"].append(name)

    for path in conftests:
        text = codeinfo.read_text(path)
        tree = codeinfo.parse_python(text) if text else None
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
                "fixture" in ast.unparse(d) for d in node.decorator_list
            ):
                env["fixtures"].append(node.name)
    env["fixtures"] = sorted(set(env["fixtures"]))[:30]

    for name in ("tests/data", "tests/fixtures", "test/data", "fixtures", "testdata"):
        if (repo / name).is_dir():
            env["test_data_dirs"].append(name)
    return env
