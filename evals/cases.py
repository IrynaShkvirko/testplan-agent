"""Evaluation cases: a change, its story, the repository it applies to, and its known defects.

A case is a directory ``evals/cases/<id>/`` holding ``case.json``. The diff and the story are
either files next to it (``change.patch``, ``story.md``) or, for ``"demo_change"``, the
generated demo change of that name. Known defects are the answer key the coverage grader uses:
each one says where the defect is, what triggers it and what a test must do to catch it, and
who wrote and who verified that label.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

CASES_DIR = Path(__file__).resolve().parent / "cases"


class CaseError(ValueError):
    """A case directory that cannot be used."""


@dataclass
class Defect:
    id: str
    summary: str  # what is wrong, in one sentence
    location: str  # file:line (or range) in the changed code
    trigger: str  # the input or state that exposes it
    caught_if: str  # what a test condition must exercise and check to catch it
    written_by: str = ""  # who drafted the label
    verified_by: str = ""  # who confirmed it; empty until a person has


@dataclass
class Case:
    id: str
    title: str
    tags: List[str]
    repo: Dict[str, Any]  # {"kind": "demo"}; other kinds come with their cases
    as_of: date
    folder: Path
    demo_change: Optional[str] = None
    defects: List[Defect] = field(default_factory=list)
    notes: str = ""

    def inputs(self, demo_changes: Optional[Path] = None) -> Dict[str, str]:
        """The diff and the story, as text."""
        if self.demo_change:
            if demo_changes is None:
                raise CaseError(f"{self.id}: needs the generated demo changes")
            base = demo_changes / self.demo_change
        else:
            base = self.folder
        try:
            diff = (base / "change.patch").read_text(encoding="utf-8")
            story = (base / "story.md").read_text(encoding="utf-8")
        except OSError as exc:
            raise CaseError(f"{self.id}: {exc}") from exc
        return {"diff": diff, "story": story}

    def display(self) -> str:
        """What the report shows as the case's prompt."""
        return f"{self.title}\n\n{self.notes}".strip()


def load_case(folder: Path) -> Case:
    try:
        raw = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CaseError(f"{folder.name}: cannot read case.json: {exc}") from exc
    try:
        case = Case(
            id=raw["id"],
            title=raw["title"],
            tags=list(raw.get("tags", [])),
            repo=dict(raw.get("repo", {"kind": "demo"})),
            as_of=date.fromisoformat(raw["as_of"]),
            folder=folder,
            demo_change=raw.get("demo_change"),
            defects=[Defect(**d) for d in raw.get("defects", [])],
            notes=raw.get("notes", ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CaseError(f"{folder.name}: malformed case.json: {exc}") from exc
    if case.id != folder.name:
        raise CaseError(f"{folder.name}: id {case.id!r} does not match its folder")
    if case.repo.get("kind") != "demo":
        raise CaseError(f"{case.id}: repository kind {case.repo.get('kind')!r} is not supported")
    return case


def load_cases(root: Path = CASES_DIR, only: Optional[List[str]] = None) -> List[Case]:
    """Every case under ``root`` in id order, or just the ones named in ``only``."""
    folders = sorted(p for p in root.iterdir() if (p / "case.json").is_file())
    cases = [load_case(p) for p in folders]
    if only:
        known = {c.id for c in cases}
        missing = sorted(set(only) - known)
        if missing:
            raise CaseError(f"unknown case(s): {', '.join(missing)}")
        cases = [c for c in cases if c.id in only]
    if not cases:
        raise CaseError(f"no cases under {root}")
    return cases
