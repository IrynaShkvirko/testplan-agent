"""Risk scoring from facts. Code computes the numbers; a model may only nudge them by one step."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .classify import NO_CODE_CLASSES
from .facts import FactStore
from .surface import AREAS

HIGH_AT = 15
MEDIUM_AT = 8

# Impact of a defect in each sensitive area (1 to 5).
AREA_IMPACT = {"money": 5, "auth": 5, "data_loss": 5, "pii": 4, "concurrency": 4, "time": 3}


@dataclass
class RiskFactor:
    name: str
    points: int
    fact_ids: List[str] = field(default_factory=list)
    detail: str = ""


@dataclass
class RiskItem:
    id: str
    title: str
    kind: str
    likelihood: int
    impact: int
    likelihood_factors: List[RiskFactor] = field(default_factory=list)
    impact_factors: List[RiskFactor] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        return self.likelihood * self.impact

    @property
    def level(self) -> str:
        return level_for(self.score)


def level_for(score: int) -> str:
    if score >= HIGH_AT:
        return "high"
    if score >= MEDIUM_AT:
        return "medium"
    return "low"


def _clamp(value: int) -> int:
    return max(1, min(5, value))


def _ids(facts: List) -> List[str]:
    return [f.id for f in facts]


def overall_likelihood(facts: FactStore) -> Tuple[int, List[RiskFactor]]:
    factors: List[RiskFactor] = []
    files = [
        f
        for f in facts.by_kind("change_file")
        if f.data.get("file_kind") in ("source", "migration", "config")
    ]
    lines = sum(int(f.data.get("added", 0)) + int(f.data.get("removed", 0)) for f in files)
    if lines >= 300:
        factors.append(RiskFactor("size", 2, _ids(files), f"{lines} lines changed"))
    elif lines >= 100:
        factors.append(RiskFactor("size", 1, _ids(files), f"{lines} lines changed"))

    symbols = facts.by_kind("symbol")
    new_code = [f for f in symbols if f.data.get("status") == "added"]
    if new_code or any(f.data.get("status") == "added" for f in files):
        factors.append(
            RiskFactor("new_code", 1, _ids(new_code) or _ids(files), "new definitions or files")
        )

    history = facts.by_kind("git_history")
    churny = [f for f in history if int(f.data.get("commits_90d", 0)) >= 5]
    if churny:
        factors.append(RiskFactor("churn", 1, _ids(churny), "5 or more commits in 90 days"))
    bugfixes = sum(int(f.data.get("bugfixes_365d", 0)) for f in history)
    if bugfixes >= 3:
        factors.append(RiskFactor("bug_history", 2, _ids(history), f"{bugfixes} earlier fixes"))
    elif bugfixes >= 1:
        factors.append(RiskFactor("bug_history", 1, _ids(history), f"{bugfixes} earlier fix(es)"))

    if len(files) >= 8:
        factors.append(RiskFactor("breadth", 1, _ids(files), f"{len(files)} files"))

    contract = [
        f for f in symbols if f.data.get("signature_change") or f.data.get("status") == "removed"
    ]
    if contract:
        factors.append(
            RiskFactor("contract_change", 1, _ids(contract), "signature changed or code removed")
        )

    untested = facts.by_kind("untested_symbol")
    if untested:
        factors.append(
            RiskFactor(
                "untested_change", 2 if len(untested) >= 3 else 1, _ids(untested), "no direct test"
            )
        )
    return _clamp(1 + sum(f.points for f in factors)), factors


def overall_impact(facts: FactStore) -> Tuple[int, List[RiskFactor]]:
    factors: List[RiskFactor] = []
    area_facts = facts.by_kind("sensitive_area")
    area_points = sum(1 if AREA_IMPACT.get(str(f.data["area"]), 1) <= 4 else 2 for f in area_facts)
    if area_points:
        factors.append(
            RiskFactor(
                "sensitive_area", min(area_points, 3), _ids(area_facts), "money, auth, data..."
            )
        )
    endpoints = [f for f in facts.by_kind("symbol") if f.data.get("endpoint")]
    if endpoints:
        factors.append(RiskFactor("public_endpoint", 1, _ids(endpoints), "HTTP endpoint touched"))
    dependents = facts.by_kind("dependent")
    if len(dependents) >= 3:
        factors.append(
            RiskFactor("blast_radius", 1, _ids(dependents), f"{len(dependents)} dependents")
        )
    change_class = facts.by_kind("change_class")
    if change_class and "migration" in change_class[0].data.get("traits", []):
        factors.append(RiskFactor("migration", 1, _ids(change_class), "schema or data migration"))
    return _clamp(1 + sum(f.points for f in factors)), factors


def assess(facts: FactStore) -> List[RiskItem]:
    """Build the ranked risk list (R1, R2, ...) from facts alone."""
    likelihood, l_factors = overall_likelihood(facts)
    impact, i_factors = overall_impact(facts)
    drafts: List[RiskItem] = []

    for fact in facts.by_kind("sensitive_area"):
        area = str(fact.data["area"])
        label = AREAS[area][0] if area in AREAS else area
        paths = ", ".join(sorted(fact.data.get("paths", []))[:3])
        drafts.append(
            RiskItem(
                id="",
                title=f"{label}: wrong behaviour in {paths}",
                kind=f"area:{area}",
                likelihood=likelihood,
                impact=_clamp(AREA_IMPACT.get(area, 3)),
                likelihood_factors=list(l_factors),
                impact_factors=[
                    RiskFactor("sensitive_area", AREA_IMPACT.get(area, 3), [fact.id], label)
                ],
                evidence=[fact.id],
            )
        )

    dependents = facts.by_kind("dependent")
    if dependents:
        drafts.append(
            RiskItem(
                id="",
                title=f"Regression in {len(dependents)} dependent module(s)",
                kind="dependents",
                likelihood=likelihood,
                impact=_clamp(
                    2
                    + (1 if len(dependents) >= 3 else 0)
                    + (1 if any(f.data.get("endpoint") for f in facts.by_kind("symbol")) else 0)
                ),
                likelihood_factors=list(l_factors),
                impact_factors=[
                    RiskFactor(
                        "blast_radius", 2, _ids(dependents), "modules that import the change"
                    )
                ],
                evidence=_ids(dependents),
            )
        )

    untested = facts.by_kind("untested_symbol")
    if untested:
        drafts.append(
            RiskItem(
                id="",
                title=f"Changed behaviour with no direct test ({len(untested)} definition(s))",
                kind="untested",
                likelihood=_clamp(likelihood + 1),
                impact=3,
                likelihood_factors=list(l_factors)
                + [
                    RiskFactor(
                        "untested_focus", 1, _ids(untested), "the risk is about missing tests"
                    )
                ],
                impact_factors=[RiskFactor("baseline", 3, [], "typical functional defect")],
                evidence=_ids(untested),
            )
        )

    contract = [
        f
        for f in facts.by_kind("symbol")
        if f.data.get("signature_change")
        or f.data.get("endpoint")
        or f.data.get("status") == "removed"
    ]
    if contract:
        drafts.append(
            RiskItem(
                id="",
                title="Contract break for callers or API clients",
                kind="contract",
                likelihood=likelihood,
                impact=4,
                likelihood_factors=list(l_factors),
                impact_factors=[
                    RiskFactor("public_contract", 4, _ids(contract), "callers depend on it")
                ],
                evidence=_ids(contract),
            )
        )

    unclear = (
        facts.by_kind("ambiguity")
        + facts.by_kind("unmapped_requirement")
        + facts.by_kind("unmapped_change")
    )
    if unclear:
        drafts.append(
            RiskItem(
                id="",
                title="Requirements misread: vague or unmatched wording",
                kind="requirements",
                likelihood=_clamp(3 + (1 if len(unclear) >= 3 else 0)),
                impact=3,
                likelihood_factors=[
                    RiskFactor(
                        "unclear_requirements",
                        2,
                        _ids(unclear),
                        "wording cannot be tested as written",
                    )
                ],
                impact_factors=[
                    RiskFactor("baseline", 3, [], "building or testing the wrong thing")
                ],
                evidence=_ids(unclear),
            )
        )

    classes = facts.by_kind("change_class")
    cls = classes[0].data.get("class") if classes else "change"
    # Criteria always need a risk to hang their conditions on, even when no code changed.
    stated = facts.by_kind("requirement")
    if not drafts and (cls not in NO_CODE_CLASSES or stated):
        files = facts.by_kind("change_file")
        drafts.append(
            RiskItem(
                id="",
                title=(
                    "Regression in the changed code"
                    if cls not in NO_CODE_CLASSES
                    else "The change does not do what its criteria ask"
                ),
                kind="general",
                likelihood=likelihood,
                impact=_clamp(max(2, impact)),
                likelihood_factors=list(l_factors),
                impact_factors=list(i_factors),
                evidence=_ids(files),
            )
        )

    drafts.sort(key=lambda r: (-r.score, r.kind, r.title))
    for n, item in enumerate(drafts, start=1):
        item.id = f"R{n}"
    return drafts


def to_dict(item: RiskItem) -> Dict[str, object]:
    return {
        "id": item.id,
        "title": item.title,
        "kind": item.kind,
        "likelihood": item.likelihood,
        "impact": item.impact,
        "score": item.score,
        "level": item.level,
        "likelihood_factors": [f.__dict__ for f in item.likelihood_factors],
        "impact_factors": [f.__dict__ for f in item.impact_factors],
        "evidence": item.evidence,
    }


def from_dict(raw: Dict[str, object]) -> RiskItem:
    def factors(key: str) -> List[RiskFactor]:
        return [RiskFactor(**f) for f in raw.get(key, [])]  # type: ignore[arg-type, union-attr]

    return RiskItem(
        id=str(raw["id"]),
        title=str(raw["title"]),
        kind=str(raw["kind"]),
        likelihood=int(raw["likelihood"]),  # type: ignore[arg-type]
        impact=int(raw["impact"]),  # type: ignore[arg-type]
        likelihood_factors=factors("likelihood_factors"),
        impact_factors=factors("impact_factors"),
        evidence=list(raw.get("evidence", [])),  # type: ignore[arg-type]
    )
