"""Change surface: what a diff touches, worked out with plain code.

Covers file kinds, the definitions changed, endpoints, signature changes, and the sensitive areas
(money, auth, personal data, concurrency, time, data loss) that raise the impact of a defect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence, Tuple

from . import codeinfo
from .diffparse import ADDED, DELETED, FileChange, Hunk

SOURCE_EXT = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".go",
    ".rb",
    ".rs",
    ".cs",
    ".kt",
    ".php",
}
CONFIG_EXT = {".yml", ".yaml", ".toml", ".ini", ".cfg", ".json", ".env", ".properties", ".conf"}
DOC_EXT = {".md", ".rst", ".txt", ".adoc"}
DEPENDENCY_NAMES = {
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "pipfile",
    "pipfile.lock",
    "poetry.lock",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "go.mod",
    "go.sum",
    "cargo.toml",
    "cargo.lock",
    "gemfile",
    "gemfile.lock",
    "pom.xml",
    "build.gradle",
}

AREAS: Dict[str, Tuple[str, re.Pattern[str]]] = {
    "money": (
        "Money and pricing",
        re.compile(
            r"\b(price|prices|pricing|amount|total|subtotal|discount|coupon|refund|payment|invoice|"
            r"currency|tax|charge|balance|fee|cents)\b",
            re.IGNORECASE,
        ),
    ),
    "auth": (
        "Authentication and permissions",
        re.compile(
            r"\b(auth|authenticate|authorize|authorisation|permission|permissions|role|roles|token|"
            r"session|login|logout|password|acl|admin|scope)\b",
            re.IGNORECASE,
        ),
    ),
    "pii": (
        "Personal data",
        re.compile(
            r"\b(email|address|phone|ssn|birthday|birth|passport|customer|first_name|last_name)\b",
            re.IGNORECASE,
        ),
    ),
    "concurrency": (
        "Concurrency",
        re.compile(
            r"\b(lock|locks|thread|threads|async|await|race|mutex|atomic|concurrent|queue|"
            r"transaction|reserve|reservation)\b",
            re.IGNORECASE,
        ),
    ),
    "time": (
        "Time and scheduling",
        re.compile(
            r"\b(timeout|expire|expires|expiry|expiration|ttl|deadline|timezone|utc|datetime|"
            r"schedule|retry|retries|minutes|seconds)\b",
            re.IGNORECASE,
        ),
    ),
    "data_loss": (
        "Data loss or migration",
        re.compile(
            r"\b(delete|drop|truncate|migrate|migration|alter\s+table|purge|backfill)\b",
            re.IGNORECASE,
        ),
    ),
}

_DEF_LINE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\((.*)\)\s*(?:->.*)?:")


@dataclass
class ChangedSymbol:
    qualname: str
    kind: str
    path: str
    start: int
    end: int
    status: str = "modified"  # modified | added | removed
    endpoint: Optional[str] = None
    signature_change: Optional[str] = None  # "old -> new" when the argument list changed


@dataclass
class ChangeSummary:
    path: str
    kind: str
    status: str
    added: int
    removed: int
    old_path: Optional[str] = None
    new_ranges: List[Tuple[int, int]] = field(default_factory=list)
    line_count: Optional[int] = None  # length of the file after the change, when known
    symbols: List[ChangedSymbol] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    areas: Dict[str, List[str]] = field(default_factory=dict)  # area -> matching terms


def classify_path(path: str) -> str:
    """Kind of file: test, migration, dependency, ci, config, docs, source or other."""
    p = PurePosixPath(path)
    parts = [x.lower() for x in p.parts]
    name = p.name.lower()
    if (
        any(x in ("tests", "test", "__tests__", "spec", "specs") for x in parts[:-1])
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts", "_test.go"))
        or name == "conftest.py"
    ):
        return "test"
    if any(x in ("migrations", "alembic", "migrate") for x in parts[:-1]) or name.endswith(".sql"):
        return "migration"
    if name in DEPENDENCY_NAMES or re.match(r"requirements.*\.txt$", name):
        return "dependency"
    if ".github" in parts and "workflows" in parts or name in (".gitlab-ci.yml", "jenkinsfile"):
        return "ci"
    ext = p.suffix.lower()
    if ext in DOC_EXT or "docs" in parts[:-1]:
        return "docs"
    if ext in SOURCE_EXT:
        return "source"
    if ext in CONFIG_EXT or name.startswith(".env") or name in ("dockerfile", "makefile"):
        return "config"
    return "other"


# ---- rebuilding the file after the change -------------------------------------------------
def _hunk_matches(lines: Sequence[str], hunk: Hunk, side: str) -> bool:
    """Do the hunk's context plus removed (side='old') or added (side='new') lines match ``lines``?"""
    start = hunk.old_start if side == "old" else hunk.new_start
    keep = (" ", "-") if side == "old" else (" ", "+")
    expected = [t for m, t in hunk.lines if m in keep]
    index = start - 1 if expected else start
    if index < 0 or index + len(expected) > len(lines):
        return False
    return all(
        lines[index + i].rstrip("\r") == expected[i].rstrip("\r") for i in range(len(expected))
    )


def after_text(change: FileChange, current: Optional[str]) -> Tuple[Optional[str], str]:
    """The file as it is after the change, rebuilt from what is on disk.

    Returns ``(text, how)``. ``how`` is ``"new-file"`` (the diff creates the file),
    ``"checkout-has-change"`` (the working tree already contains it), ``"applied"`` (the diff
    applied cleanly to the working tree, which is at the base revision), or ``"unknown"``.
    """
    if change.status == DELETED:
        return None, "deleted"
    if change.status == ADDED:
        text = "\n".join(t for h in change.hunks for m, t in h.lines if m in ("+", " "))
        return text + "\n", "new-file"
    if current is None:
        return None, "unknown"
    lines = current.split("\n")
    if change.hunks and all(_hunk_matches(lines, h, "new") for h in change.hunks):
        return current, "checkout-has-change"
    if change.hunks and all(_hunk_matches(lines, h, "old") for h in change.hunks):
        out: List[str] = []
        cursor = 0
        for hunk in change.hunks:
            begin = hunk.old_start - 1 if hunk.old_len else hunk.old_start
            out.extend(lines[cursor:begin])
            out.extend(t for m, t in hunk.lines if m in (" ", "+"))
            cursor = begin + sum(1 for m, _ in hunk.lines if m in (" ", "-"))
        out.extend(lines[cursor:])
        return "\n".join(out), "applied"
    return None, "unknown"


# ---- summaries ----------------------------------------------------------------------------
def _def_signatures(lines: Sequence[Tuple[int, str]]) -> Dict[str, str]:
    found: Dict[str, str] = {}
    for _, text in lines:
        match = _DEF_LINE.match(text)
        if match:
            found[match.group(1)] = re.sub(r"\s+", " ", match.group(2)).strip()
    return found


def _areas_for(text: str) -> Dict[str, List[str]]:
    found: Dict[str, List[str]] = {}
    for key, (_, pattern) in AREAS.items():
        hits: List[str] = []
        for match in pattern.finditer(text):
            term = match.group(0).lower()
            if term not in hits:
                hits.append(term)
        if hits:
            found[key] = hits[:5]
    return found


def summarize(change: FileChange, repo: Optional[Path]) -> ChangeSummary:
    kind = classify_path(change.path)
    summary = ChangeSummary(
        path=change.path,
        kind=kind,
        status=change.status,
        added=change.added,
        removed=change.removed,
        old_path=change.old_path,
        new_ranges=[
            (h.new_start, max(h.new_start + h.new_len - 1, h.new_start)) for h in change.hunks
        ],
    )

    current: Optional[str] = None
    if repo is not None and change.status != ADDED:
        current = codeinfo.read_text(repo / change.path)
    text_after, how = after_text(change, current)
    if text_after is not None:
        summary.line_count = codeinfo.line_range(text_after)[1]

    if kind in ("source", "test") and change.path.endswith(".py"):
        summary.symbols = _python_symbols(change, text_after, how)
    elif kind == "source":
        summary.symbols = _section_symbols(change)

    # Text scanned for sensitive areas: path, symbol names and the lines that changed.
    haystack = " ".join(
        [change.path.replace("/", " "), change.added_text(), change.removed_text()]
        + [s.qualname for s in summary.symbols]
    )
    if kind in ("source", "migration", "config"):
        summary.areas = _areas_for(haystack)
    if kind == "migration":
        summary.areas.setdefault("data_loss", ["migration"])
    if kind in ("config", "dependency", "ci"):
        quoted = [t.strip() for _, t in _changed_lines(change)][:6]
        summary.notes.extend(f"changed: {q[:80]}" for q in quoted if q)
    if how == "unknown" and change.path.endswith(".py"):
        summary.notes.append(
            "could not rebuild the file from the working tree; symbols are approximate"
        )
    return summary


def _changed_lines(change: FileChange) -> List[Tuple[int, str]]:
    lines: List[Tuple[int, str]] = []
    for hunk in change.hunks:
        lines.extend(hunk.added_lines())
    return lines


def _python_symbols(change: FileChange, text_after: Optional[str], how: str) -> List[ChangedSymbol]:
    symbols: List[ChangedSymbol] = []
    removed_sigs = _def_signatures([x for h in change.hunks for x in h.removed_lines()])
    added_sigs = _def_signatures([x for h in change.hunks for x in h.added_lines()])

    if text_after is not None:
        defs = codeinfo.definitions(text_after, change.path)
        seen: Dict[str, ChangedSymbol] = {}
        module_touched = False
        for line in change.touched_new_lines():
            sym = codeinfo.innermost(defs, line)
            if sym is None:
                module_touched = True
                continue
            if sym.qualname in seen:
                continue
            is_new = change.status == ADDED or (
                sym.name in added_sigs and sym.name not in removed_sigs and sym.kind != "class"
            )
            changed = ChangedSymbol(
                qualname=sym.qualname,
                kind=sym.kind,
                path=change.path,
                start=sym.start,
                end=sym.end,
                status="added" if is_new else "modified",
                endpoint=sym.endpoint,
            )
            old, new = removed_sigs.get(sym.name), added_sigs.get(sym.name)
            if old is not None and new is not None and old != new:
                changed.signature_change = f"({old}) -> ({new})"
            seen[sym.qualname] = changed
        symbols.extend(seen.values())
        if module_touched and change.status != ADDED:
            symbols.append(ChangedSymbol("<module>", "module", change.path, 1, 1, "modified"))
    else:
        for name, args in added_sigs.items():
            changed = ChangedSymbol(name, "function", change.path, 0, 0, "added")
            if name in removed_sigs:
                changed.status = "modified"
                if removed_sigs[name] != args:
                    changed.signature_change = f"({removed_sigs[name]}) -> ({args})"
            symbols.append(changed)

    for name in removed_sigs:
        if name not in added_sigs and not any(s.qualname.endswith(name) for s in symbols):
            symbols.append(ChangedSymbol(name, "function", change.path, 0, 0, "removed"))
    return symbols


def _section_symbols(change: FileChange) -> List[ChangedSymbol]:
    out: List[ChangedSymbol] = []
    for hunk in change.hunks:
        name = hunk.section.strip()
        if name and all(s.qualname != name for s in out):
            out.append(
                ChangedSymbol(name[:80], "unknown", change.path, hunk.new_start, hunk.new_start)
            )
    return out
