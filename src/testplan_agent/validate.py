"""Validators: checks in code that decide whether a plan is grounded in the context bundle.

An error means the plan cannot be trusted as written and is sent back for repair. A warning
is shown to the reviewer but does not block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .bundle import ContextBundle
from .schema import PLAN_SCHEMA, TestPlan, schema_errors

ERROR = "error"
WARNING = "warning"

_FACT_ID = re.compile(r"^F\d+$")
_FILE_REF = re.compile(r"^(?P<path>[^\s:][^:]*):(?P<start>\d+)(?:-(?P<end>\d+))?$")
DEFAULT_MAX_CASES_PER_RISK = 8


@dataclass
class Issue:
    code: str
    severity: str
    where: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "where": self.where,
            "message": self.message,
        }

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code} at {self.where}: {self.message}"


def has_errors(issues: List[Issue]) -> bool:
    return any(i.severity == ERROR for i in issues)


def check_shape(raw: Any) -> List[Issue]:
    """Structural problems: wrong types, missing fields, values outside the allowed sets."""
    return [Issue("schema", ERROR, "plan", message) for message in schema_errors(raw, PLAN_SCHEMA)]


def _file_ref_problem(ref: str, bundle: ContextBundle, repo: Optional[Path]) -> Optional[str]:
    match = _FILE_REF.match(ref)
    if not match:
        return "not a fact id (F12) or a file:line reference"
    path, start = match.group("path"), int(match.group("start"))
    end = int(match.group("end") or start)
    if start < 1 or end < start:
        return "line numbers must start at 1 and not run backwards"
    change = bundle.change_by_path().get(path)
    if change is not None:
        if change.line_count is not None:
            if end > change.line_count:
                return f"{path} has {change.line_count} lines after the change"
            return None
        if any(lo - 5 <= start and end <= hi + 5 for lo, hi in change.new_ranges):
            return None
        return f"{path}:{start}-{end} is outside every changed hunk"
    if repo is not None:
        target = (repo / path).resolve()
        try:
            target.relative_to(repo.resolve())
        except ValueError:
            return "path escapes the repository"
        if target.is_file():
            try:
                lines = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                return f"cannot read {path}"
            return None if end <= lines else f"{path} has only {lines} lines"
    return f"{path} is not a changed file or a file in the repository"


def validate_plan(
    plan: TestPlan,
    bundle: ContextBundle,
    repo: Optional[Path] = None,
    max_cases_per_risk: int = DEFAULT_MAX_CASES_PER_RISK,
) -> List[Issue]:
    """Check a well-formed plan against the facts it was supposed to be built from."""
    issues: List[Issue] = []

    def err(code: str, where: str, message: str) -> None:
        issues.append(Issue(code, ERROR, where, message))

    def warn(code: str, where: str, message: str) -> None:
        issues.append(Issue(code, WARNING, where, message))

    def check_evidence(where: str, refs: List[str]) -> None:
        for ref in refs:
            if _FACT_ID.match(ref):
                if ref not in bundle.facts:
                    err("unknown_fact", where, f"{ref} is not a fact in the context bundle")
            else:
                problem = _file_ref_problem(ref, bundle, repo)
                if problem:
                    err("bad_file_ref", where, f"{ref}: {problem}")

    # -- ids are unique
    for label, ids in (
        ("case", [c.id for c in plan.cases]),
        ("risk", [r.id for r in plan.risks]),
        ("question", [q.id for q in plan.open_questions]),
    ):
        seen: Set[str] = set()
        for ident in ids:
            if ident in seen:
                err("duplicate_id", f"{label} {ident}", f"{label} id {ident} is used twice")
            seen.add(ident)

    # -- risks: must come from the bundle; a model may only nudge them by one step, with a reason
    known_risks = bundle.risk_by_id()
    for r in plan.risks:
        where = f"risk {r.id}"
        base = known_risks.get(r.id)
        if base is None:
            err("unknown_risk", where, f"{r.id} is not a risk computed from the facts")
            continue
        adj = r.adjustment
        if r.likelihood != base.likelihood + adj.likelihood_delta:
            err(
                "risk_mismatch",
                where,
                f"likelihood {r.likelihood} != computed {base.likelihood} + adjustment {adj.likelihood_delta}",
            )
        if r.impact != base.impact + adj.impact_delta:
            err(
                "risk_mismatch",
                where,
                f"impact {r.impact} != computed {base.impact} + adjustment {adj.impact_delta}",
            )
        if (adj.likelihood_delta or adj.impact_delta) and not adj.reason.strip():
            err("adjustment_without_reason", where, "a risk was adjusted without a written reason")
        check_evidence(where, r.evidence)
    missing = [rid for rid in known_risks if rid not in {r.id for r in plan.risks}]
    for rid in missing:
        if known_risks[rid].level == "high":
            err("risk_dropped", f"risk {rid}", f"high risk {rid} is missing from the plan")

    # -- cases
    criteria = {r.id: r for r in bundle.criteria()}
    plan_risk_ids = {r.id for r in plan.risks}
    known_tests = bundle.existing_ids()
    quarantined = {t.nodeid for t in bundle.existing_tests if t.quarantined}
    per_risk: Dict[str, int] = {}
    covered: Set[str] = set()
    for c in plan.cases:
        where = f"case {c.id}"
        check_evidence(where, c.evidence)
        if c.requirement == "none":
            if not c.requirement_reason.strip():
                err(
                    "missing_requirement_reason",
                    where,
                    "requirement is 'none' but no reason is given",
                )
        elif c.requirement in criteria:
            covered.add(c.requirement)
        else:
            err(
                "unknown_requirement",
                where,
                f"{c.requirement} is not an acceptance criterion of this change",
            )
        if c.risk not in plan_risk_ids:
            err(
                "unknown_risk",
                where,
                f"case is linked to {c.risk}, which is not in the plan's risks",
            )
        else:
            per_risk[c.risk] = per_risk.get(c.risk, 0) + 1
            level = known_risks[c.risk].level if c.risk in known_risks else None
            if c.priority == "P0" and level == "low":
                warn("priority_mismatch", where, f"P0 case linked to low risk {c.risk}")
        if c.existing_coverage != "none":
            if c.existing_coverage not in known_tests:
                err(
                    "unknown_test",
                    where,
                    f"{c.existing_coverage} is not an existing test found by the scan",
                )
            elif c.existing_coverage in quarantined:
                warn(
                    "quarantined_coverage",
                    where,
                    f"{c.existing_coverage} is quarantined as flaky and is not reliable coverage",
                )
        if c.priority in ("P0", "P1") and (not c.steps or not c.expected.strip()):
            err("missing_steps", where, f"{c.priority} case needs steps and an expected result")
        if c.confidence != "high" and not c.confidence_reason.strip():
            warn("missing_confidence_reason", where, "confidence below high without a reason")

    for rid, base in known_risks.items():
        behavioural = base.kind.startswith("area:") or base.kind in (
            "contract",
            "dependents",
            "general",
        )
        if (
            base.level == "high"
            and behavioural
            and rid in plan_risk_ids
            and per_risk.get(rid, 0) == 0
        ):
            warn(
                "risk_without_cases",
                f"risk {rid}",
                f"high risk {rid} has no test condition linked to it",
            )

    for rid, count in sorted(per_risk.items()):
        if count > max_cases_per_risk:
            warn(
                "too_many_cases",
                f"risk {rid}",
                f"{count} cases for one risk; the cap is {max_cases_per_risk}",
            )

    # -- every acceptance criterion needs at least one condition
    for cid in criteria:
        if cid not in covered:
            err("uncovered_requirement", f"requirement {cid}", f"{cid} has no test condition")

    # -- regression scope points at tests that exist
    for item in plan.regression_scope:
        if item.test not in known_tests:
            err("unknown_test", f"regression {item.test}", "not an existing test found by the scan")
        check_evidence(f"regression {item.test}", item.evidence)

    # -- questions cite evidence, and every flagged ambiguity is raised
    cited: Set[str] = set()
    for q in plan.open_questions:
        check_evidence(f"question {q.id}", q.evidence)
        cited.update(q.evidence)
    for kind in ("ambiguity", "unmapped_requirement", "unmapped_change"):
        for fact in bundle.facts.by_kind(kind):
            if fact.id not in cited:
                warn(
                    "unraised_flag",
                    f"fact {fact.id}",
                    f"not raised as an open question: {fact.text}",
                )

    if plan.summary.change_class != bundle.change_class.get("class"):
        warn(
            "class_mismatch",
            "summary",
            f"plan says '{plan.summary.change_class}', the classifier found '{bundle.change_class.get('class')}'",
        )
    return issues
