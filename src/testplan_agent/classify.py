"""Change class and traits, decided by rules so the plan template is not a model's guess."""

from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from .surface import ChangeSummary

_BUGFIX = re.compile(
    r"\b(fix|fixes|fixed|bug|bugfix|hotfix|regression|defect|crash|incorrect)\b", re.I
)
_REFACTOR = re.compile(
    r"\b(refactor|refactoring|cleanup|clean up|rename|restructure|no behaviou?r change)\b", re.I
)

CLASSES = (
    "feature",
    "bugfix",
    "refactor",
    "config",
    "migration",
    "dependency",
    "test-only",
    "docs",
    "change",
)


def classify(
    changes: Sequence[ChangeSummary], title: str, description: str, has_criteria: bool
) -> Tuple[str, List[str], List[str]]:
    """Return (class, traits, reasons)."""
    kinds = {c.kind for c in changes}
    source = [c for c in changes if c.kind == "source"]
    symbols = [s for c in source for s in c.symbols]
    traits: List[str] = []
    reasons: List[str] = []

    if "migration" in kinds:
        traits.append("migration")
    if "dependency" in kinds:
        traits.append("dependency")
    if kinds & {"config", "ci"}:
        traits.append("config")
    if any(s.endpoint and s.status == "added" for s in symbols):
        traits.append("new_endpoint")
    elif any(s.endpoint for s in symbols):
        traits.append("changed_endpoint")
    if any(c.status == "added" for c in source):
        traits.append("new_module")
    if any(s.signature_change for s in symbols):
        traits.append("signature_change")
    if any(s.status == "removed" for s in symbols):
        traits.append("removed_code")
    if "test" in kinds:
        traits.append("tests_changed")

    if kinds <= {"docs"}:
        return "docs", traits, ["only documentation changed"]
    if kinds <= {"test"}:
        return "test-only", traits, ["only test files changed"]
    if not source:
        if "migration" in kinds:
            return "migration", traits, ["schema or data migration without application code"]
        if "dependency" in kinds:
            return "dependency", traits, ["dependency manifest changed without application code"]
        return "config", traits, ["only configuration or CI files changed"]

    text = f"{title}\n{description}"
    added_symbols = [s for s in symbols if s.status == "added"]
    if _BUGFIX.search(title) and not added_symbols:
        reasons.append("title describes a fix and no new definitions were added")
        return "bugfix", traits, reasons
    if _REFACTOR.search(text) and not has_criteria and not added_symbols:
        reasons.append("description says refactor and there are no acceptance criteria")
        return "refactor", traits, reasons
    if added_symbols or "new_module" in traits:
        reasons.append("new definitions or a new module were added")
        return "feature", traits, reasons
    if has_criteria:
        reasons.append("existing code changed under new acceptance criteria")
        return "feature", traits, reasons
    if _BUGFIX.search(text):
        reasons.append("description mentions a fix")
        return "bugfix", traits, reasons
    reasons.append("existing code changed with no stated criteria")
    return "change", traits, reasons
