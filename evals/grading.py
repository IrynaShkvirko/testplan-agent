"""Grades that need no judgment: computed from the plan and the checks, so free and repeatable.

Coverage of known defects needs judgment: a person grades it (evals/coverage.py) and the grades
are merged into the same rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from testplan_agent.bundle import ContextBundle
from testplan_agent.planner import bundle_hash, parse_model_json
from testplan_agent.schema import TestPlan
from testplan_agent.validate import check_shape, validate_plan

# Checks that mean a citation did not resolve.
_CITATION_CODES = ("unknown_fact", "bad_file_ref")

# What each metric is, for the report (_state.json) and for readers of the results.
# Coverage comes first: the report's headline is the first metric (none is "binary", which
# would take precedence). It is graded by a person; see evals/coverage.py.
METRICS: List[Dict[str, Any]] = [
    {"id": "coverage", "label": "Coverage", "kind": "float", "scale": 1},
    {"id": "coverage_lenient", "label": "Cov. lenient", "kind": "float", "scale": 1},
    {"id": "checks_clean", "label": "Checks clean", "kind": "float", "scale": 1},
    {"id": "first_try_clean", "label": "First try", "kind": "float", "scale": 1},
    {"id": "citations_first", "label": "Citations ok", "kind": "float", "scale": 1},
]


def citation_accuracy(answer: str, bundle: ContextBundle, repo: Optional[Path]) -> Optional[float]:
    """Share of an answer's citations that resolve, before any repair.

    None when the answer is not a well-formed plan: there is nothing to count, which is not
    the same as every citation being wrong.
    """
    try:
        data = parse_model_json(answer)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    data = dict(data)
    data["meta"] = {"generator": "eval", "created": "-", "bundle_sha256": bundle_hash(bundle)}
    data.pop("validation", None)
    if check_shape(data):
        return None
    plan = TestPlan.from_dict(data)
    total = sum(
        len(item.evidence)
        for group in (plan.risks, plan.cases, plan.regression_scope, plan.open_questions)
        for item in group
    )
    if total == 0:
        return None
    broken = sum(i.code in _CITATION_CODES for i in validate_plan(plan, bundle, repo))
    return round((total - broken) / total, 4)


def grade(
    plan: TestPlan, attempts: int, first_answer: str, bundle: ContextBundle, repo: Optional[Path]
) -> Dict[str, float]:
    errors = sum(v.get("severity") == "error" for v in plan.validation)
    grades: Dict[str, float] = {
        "checks_clean": float(errors == 0),
        "first_try_clean": float(errors == 0 and attempts == 1),
    }
    accuracy = citation_accuracy(first_answer, bundle, repo)
    if accuracy is not None:
        grades["citations_first"] = accuracy
    return grades
