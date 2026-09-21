"""Deterministic extraction of acceptance criteria, non-goals and vague wording from a spec.

v0.1 reads explicit structure: criteria under an "Acceptance criteria" style heading, bullets
that use must/shall wording, Given/When/Then blocks, and "Out of scope" sections. Free-text
requirements are left to the model in a later version; anything ambiguous is flagged, not guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

CRITERION = "criterion"
NON_GOAL = "non_goal"

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.*\S)\s*$")
_LABEL = re.compile(r"^\**(AC|REQ|R)[-_ ]?(\d+)\**\s*[:.)-]?\s*(.*)$", re.IGNORECASE)
_GHERKIN_START = re.compile(r"^\s*(?:[-*]\s+)?(Given)\b", re.IGNORECASE)
_GHERKIN_CONT = re.compile(r"^\s*(?:[-*]\s+)?(When|Then|And|But)\b", re.IGNORECASE)
_MODAL = re.compile(r"\b(must|shall|should|will|needs? to|has to|have to)\b", re.IGNORECASE)

_CRITERIA_HEADINGS = re.compile(
    r"acceptance|criteria|requirements?|definition of done|expected behaviou?r", re.IGNORECASE
)
_NON_GOAL_HEADINGS = re.compile(
    r"out of scope|non[- ]?goals?|not in scope|will not|won'?t|exclusions?", re.IGNORECASE
)

# Wording that cannot be tested as written. Each needs a number, a rule or an example.
VAGUE_TERMS = [
    "reasonable",
    "reasonably",
    "appropriate",
    "appropriately",
    "as needed",
    "as necessary",
    "etc",
    "and so on",
    "fast",
    "quick",
    "quickly",
    "soon",
    "user-friendly",
    "easy",
    "easily",
    "simple",
    "robust",
    "flexible",
    "efficient",
    "efficiently",
    "gracefully",
    "seamless",
    "seamlessly",
    "secure",
    "tbd",
    "tbc",
    "some",
    "several",
    "many",
    "large",
    "small",
    "if possible",
    "where possible",
    "possibly",
    "maybe",
    "might",
    "approximately",
]
_VAGUE = re.compile(
    r"(?<![\w-])("
    + "|".join(re.escape(t) for t in sorted(VAGUE_TERMS, key=len, reverse=True))
    + r")(?![\w-])",
    re.IGNORECASE,
)


@dataclass
class Requirement:
    id: str
    text: str
    kind: str = CRITERION
    line: int = 0
    label: Optional[str] = None
    fact_id: Optional[str] = None


@dataclass
class Ambiguity:
    requirement_id: str
    term: str
    text: str
    fact_id: Optional[str] = None


@dataclass
class Story:
    title: str = ""
    requirements: List[Requirement] = field(default_factory=list)
    ambiguities: List[Ambiguity] = field(default_factory=list)

    @property
    def criteria(self) -> List[Requirement]:
        return [r for r in self.requirements if r.kind == CRITERION]

    @property
    def non_goals(self) -> List[Requirement]:
        return [r for r in self.requirements if r.kind == NON_GOAL]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("**", "").replace("`", "")).strip()


def find_vague_terms(text: str) -> List[str]:
    seen: List[str] = []
    for match in _VAGUE.finditer(text):
        term = match.group(1).lower()
        if term not in seen:
            seen.append(term)
    return seen


def parse_story(text: str) -> Story:
    """Extract structure from a Markdown story, issue or pull request description."""
    story = Story()
    mode = "none"  # none | criteria | non_goal
    pending: List[Requirement] = []  # requirements found in order
    used_ids = set()
    counter = 0
    current: Optional[Requirement] = None
    current_indent = 0
    gherkin_open = False

    def new_id(label_prefix: Optional[str], number: Optional[int]) -> str:
        nonlocal counter
        if label_prefix and number is not None:
            candidate = f"{label_prefix.upper()}-{number}"
            if candidate not in used_ids:
                return candidate
        counter += 1
        while f"AC-{counter}" in used_ids:
            counter += 1
        return f"AC-{counter}"

    def add(raw: str, kind: str, line_no: int) -> Requirement:
        label = None
        number = None
        prefix = None
        match = _LABEL.match(raw)
        if match and kind == CRITERION:
            prefix, number, raw = match.group(1), int(match.group(2)), match.group(3)
            label = f"{prefix.upper()}-{number}"
        if kind == NON_GOAL:
            ident = f"NG-{sum(1 for r in pending if r.kind == NON_GOAL) + 1}"
        else:
            ident = new_id(prefix, number)
        used_ids.add(ident)
        req = Requirement(id=ident, text=_clean(raw), kind=kind, line=line_no, label=label)
        pending.append(req)
        return req

    lines = text.splitlines()
    for idx, line in enumerate(lines, start=1):
        heading = _HEADING.match(line)
        if heading:
            title = heading.group(1)
            if not story.title and line.lstrip().startswith("# "):
                story.title = _clean(title)
            if _NON_GOAL_HEADINGS.search(title):
                mode = "non_goal"
            elif _CRITERIA_HEADINGS.search(title):
                mode = "criteria"
            else:
                mode = "none"
            current = None
            gherkin_open = False
            continue
        if not line.strip():
            gherkin_open = False
            continue

        if mode in ("criteria", "non_goal"):
            kind = CRITERION if mode == "criteria" else NON_GOAL
            if kind == CRITERION and _GHERKIN_START.match(line):
                stripped = re.sub(r"^\s*(?:[-*]\s+)?", "", line)
                current = add(stripped, kind, idx)
                gherkin_open = True
                continue
            if kind == CRITERION and gherkin_open and _GHERKIN_CONT.match(line) and current:
                current.text = _clean(current.text + " " + re.sub(r"^\s*(?:[-*]\s+)?", "", line))
                continue
            bullet = _BULLET.match(line)
            if bullet:
                indent = len(bullet.group(1).expandtabs(4))
                if current is not None and indent > current_indent and current.kind == kind:
                    current.text = _clean(current.text + " " + bullet.group(2))
                else:
                    current = add(bullet.group(2), kind, idx)
                    current_indent = indent
                gherkin_open = False
                continue
            if current is not None and line.startswith((" ", "\t")):
                current.text = _clean(current.text + " " + line)
                continue
            # a plain sentence in a criteria section counts only if it is modal
            if kind == CRITERION and _MODAL.search(line):
                current = add(line, kind, idx)
                current_indent = 0
            continue

    if not any(r.kind == CRITERION for r in pending):
        # No criteria section: fall back to bullets that use must/shall/should wording.
        for idx, line in enumerate(lines, start=1):
            bullet = _BULLET.match(line)
            if bullet and _MODAL.search(bullet.group(2)):
                add(bullet.group(2), CRITERION, idx)

    story.requirements = pending
    for req in story.criteria:
        for term in find_vague_terms(req.text):
            story.ambiguities.append(
                Ambiguity(
                    requirement_id=req.id,
                    term=term,
                    text=f"{req.id} says '{term}' without a number, rule or example",
                )
            )
    return story
