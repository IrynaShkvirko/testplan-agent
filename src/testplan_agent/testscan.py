"""Existing tests: which ones already touch the changed code, found by static analysis."""

from __future__ import annotations

import ast
import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from . import codeinfo
from .surface import ChangeSummary, classify_path

DIRECT = "direct"  # references a changed symbol by name and links to the changed module
MODULE = "module"  # only imports, or is named after, the changed module


@dataclass
class ExistingTest:
    nodeid: str
    path: str
    strength: str
    modules: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    quarantined: bool = False


@dataclass
class TestFile:
    __test__ = False  # not a pytest test class
    path: str
    imports: Set[str]
    tests: Dict[str, Set[str]]  # nodeid -> names referenced in the test body


def load_quarantine(path: Optional[Path]) -> List[str]:
    """Entries of a flaky-quarantine list (node ids or glob patterns); empty if unreadable."""
    if path is None or not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tests = raw.get("tests") if isinstance(raw, dict) else None
    return sorted(tests) if isinstance(tests, dict) else []


def is_quarantined(nodeid: str, entries: Sequence[str]) -> bool:
    for entry in entries:
        if entry == nodeid or nodeid.startswith(entry + "[") or entry.startswith(nodeid + "["):
            return True
        if any(ch in entry for ch in "*?[") and fnmatch.fnmatchcase(nodeid, entry):
            return True
    return False


def _is_test_file(rel: str) -> bool:
    return rel.endswith(".py") and classify_path(rel) == "test" and Path(rel).name != "conftest.py"


def scan_test_files(repo: Path) -> List[TestFile]:
    files: List[TestFile] = []
    for path in codeinfo.iter_python_files(repo):
        rel = path.relative_to(repo).as_posix()
        if not _is_test_file(rel):
            continue
        text = codeinfo.read_text(path)
        tree = codeinfo.parse_python(text) if text is not None else None
        if tree is None or text is None:
            continue
        tests: Dict[str, Set[str]] = {}
        for node in tree.body:  # type: ignore[attr-defined]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test"
            ):
                tests[f"{rel}::{node.name}"] = codeinfo.referenced_names(node)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                for item in node.body:
                    if isinstance(
                        item, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ) and item.name.startswith("test"):
                        tests[f"{rel}::{node.name}::{item.name}"] = codeinfo.referenced_names(item)
        files.append(TestFile(path=rel, imports=codeinfo.imported_modules(text, rel), tests=tests))
    return files


def _simple(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def match_existing(
    changes: Sequence[ChangeSummary],
    files: Sequence[TestFile],
    quarantine: Sequence[str] = (),
) -> List[ExistingTest]:
    """Link each existing test to the changed source code it exercises."""
    sources = [c for c in changes if c.kind == "source" and c.path.endswith(".py")]
    found: Dict[str, ExistingTest] = {}
    for src in sources:
        module = codeinfo.module_name(src.path)
        base = module.rsplit(".", 1)[-1]
        names = {
            _simple(s.qualname) for s in src.symbols if s.kind != "module" and s.status != "added"
        }
        for tf in files:
            imports_it = any(imp == module or imp.startswith(module + ".") for imp in tf.imports)
            named_like = Path(tf.path).stem in (f"test_{base}", f"{base}_test")
            if not (imports_it or named_like):
                continue
            for nodeid, refs in tf.tests.items():
                hit = sorted(names & refs)
                entry = found.get(nodeid)
                if entry is None:
                    entry = ExistingTest(
                        nodeid=nodeid,
                        path=tf.path,
                        strength=MODULE,
                        quarantined=is_quarantined(nodeid, quarantine),
                    )
                    found[nodeid] = entry
                if module not in entry.modules:
                    entry.modules.append(module)
                if hit:
                    entry.strength = DIRECT
                    for name in hit:
                        if name not in entry.symbols:
                            entry.symbols.append(name)
                    entry.reasons.append(f"calls {', '.join(hit)} from {src.path}")
                elif imports_it:
                    entry.reasons.append(f"imports {module}")
                else:
                    entry.reasons.append(f"file is named for {module}")
    return sorted(found.values(), key=lambda t: (t.strength != DIRECT, t.nodeid))
