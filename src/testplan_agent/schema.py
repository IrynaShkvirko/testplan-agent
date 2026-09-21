"""The test plan data model, its JSON Schema, and a small schema validator (no dependencies)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

LEVELS = ["unit", "integration", "e2e", "exploratory"]
PRIORITIES = ["P0", "P1", "P2"]
AUTOMATION = ["now", "later", "manual"]
CONFIDENCE = ["high", "medium", "low"]
TECHNIQUES = [
    "equivalence",
    "boundary",
    "negative",
    "error_handling",
    "state_transition",
    "permission",
    "idempotency",
    "concurrency",
    "compatibility",
    "migration",
    "data_validation",
    "performance",
    "regression",
    "exploratory",
]
QUESTION_KINDS = ["ambiguity", "unmapped", "assumption", "missing_info"]

_STR = {"type": "string", "minLength": 1}
_STR_LIST = {"type": "array", "items": _STR}
_EVIDENCE = {"type": "array", "items": _STR, "minItems": 1}  # every claim cites something


def _obj(
    props: Dict[str, Any], required: Optional[List[str]] = None, desc: str = ""
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "type": "object",
        "properties": props,
        "required": required if required is not None else list(props),
        "additionalProperties": False,
    }
    if desc:
        out["description"] = desc
    return out


PLAN_SCHEMA: Dict[str, Any] = _obj(
    {
        "schema_version": {"type": "integer", "enum": [SCHEMA_VERSION]},
        "meta": _obj(
            {
                "generator": _STR,
                "model": {"type": "string"},
                "created": _STR,
                "bundle_sha256": {"type": "string"},
            },
            required=["generator", "created"],
        ),
        "summary": _obj(
            {
                "change_class": _STR,
                "text": _STR,
                "in_scope": _STR_LIST,
                "out_of_scope": _STR_LIST,
            }
        ),
        "risks": {
            "type": "array",
            "items": _obj(
                {
                    "id": {"type": "string", "pattern": r"^R\d+$"},
                    "title": _STR,
                    "likelihood": {"type": "integer", "minimum": 1, "maximum": 5},
                    "impact": {"type": "integer", "minimum": 1, "maximum": 5},
                    "adjustment": _obj(
                        {
                            "likelihood_delta": {"type": "integer", "minimum": -1, "maximum": 1},
                            "impact_delta": {"type": "integer", "minimum": -1, "maximum": 1},
                            "reason": {"type": "string"},
                        }
                    ),
                    "evidence": _EVIDENCE,
                }
            ),
        },
        "cases": {
            "type": "array",
            "items": _obj(
                {
                    "id": {"type": "string", "pattern": r"^TC-\d+$"},
                    "title": _STR,
                    "requirement": _STR,
                    "requirement_reason": {"type": "string"},
                    "evidence": _EVIDENCE,
                    "technique": {"type": "string", "enum": TECHNIQUES},
                    "level": {"type": "string", "enum": LEVELS},
                    "priority": {"type": "string", "enum": PRIORITIES},
                    "risk": {"type": "string", "pattern": r"^R\d+$"},
                    "automation": {"type": "string", "enum": AUTOMATION},
                    "existing_coverage": _STR,
                    "steps": _STR_LIST,
                    "expected": {"type": "string"},
                    "confidence": {"type": "string", "enum": CONFIDENCE},
                    "confidence_reason": {"type": "string"},
                }
            ),
        },
        "regression_scope": {
            "type": "array",
            "items": _obj({"test": _STR, "reason": _STR, "evidence": _EVIDENCE}),
        },
        "open_questions": {
            "type": "array",
            "items": _obj(
                {
                    "id": {"type": "string", "pattern": r"^Q\d+$"},
                    "kind": {"type": "string", "enum": QUESTION_KINDS},
                    "text": _STR,
                    "evidence": _EVIDENCE,
                }
            ),
        },
        "assumptions": _STR_LIST,
        "environment_needs": _STR_LIST,
        "exit_criteria": _STR_LIST,
        "validation": {
            "type": "array",
            "items": _obj(
                {"code": _STR, "severity": _STR, "where": {"type": "string"}, "message": _STR}
            ),
        },
    },
    desc="A draft test plan. Every claim must cite evidence: fact ids (F12) or file:line.",
)
PLAN_SCHEMA["required"] = [k for k in PLAN_SCHEMA["required"] if k != "validation"]


# ---- a small JSON Schema validator: the subset used above -----------------------------------
def schema_errors(instance: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    errors: List[str] = []
    expected = schema.get("type")
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    }
    if expected and not checks[expected](instance):
        return [f"{path}: expected {expected}, got {type(instance).__name__}"]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']}")
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            errors.append(f"{path}: must not be empty")
        pattern = schema.get("pattern")
        if pattern and not re.match(pattern, instance):
            errors.append(f"{path}: {instance!r} does not match {pattern}")
    if isinstance(instance, int) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} is below {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} is above {schema['maximum']}")
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(instance):
                errors.extend(schema_errors(item, item_schema, f"{path}[{i}]"))
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in instance:
                errors.append(f"{path}: missing required field '{name}'")
        if schema.get("additionalProperties") is False:
            for name in instance:
                if name not in props:
                    errors.append(f"{path}: unexpected field '{name}'")
        for name, sub in props.items():
            if name in instance:
                errors.extend(schema_errors(instance[name], sub, f"{path}.{name}"))
    return errors


# ---- the model --------------------------------------------------------------------------------
@dataclass
class Adjustment:
    likelihood_delta: int = 0
    impact_delta: int = 0
    reason: str = ""


@dataclass
class PlanRisk:
    id: str
    title: str
    likelihood: int
    impact: int
    adjustment: Adjustment = field(default_factory=Adjustment)
    evidence: List[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        return self.likelihood * self.impact


@dataclass
class TestCase:
    __test__ = False  # not a pytest test class
    id: str
    title: str
    requirement: str
    evidence: List[str]
    technique: str
    level: str
    priority: str
    risk: str
    automation: str
    existing_coverage: str
    expected: str = ""
    steps: List[str] = field(default_factory=list)
    requirement_reason: str = ""
    confidence: str = "medium"
    confidence_reason: str = ""


@dataclass
class RegressionItem:
    test: str
    reason: str
    evidence: List[str] = field(default_factory=list)


@dataclass
class Question:
    id: str
    kind: str
    text: str
    evidence: List[str] = field(default_factory=list)


@dataclass
class Summary:
    change_class: str
    text: str
    in_scope: List[str] = field(default_factory=list)
    out_of_scope: List[str] = field(default_factory=list)


@dataclass
class TestPlan:
    __test__ = False  # not a pytest test class
    meta: Dict[str, str]
    summary: Summary
    risks: List[PlanRisk]
    cases: List[TestCase]
    regression_scope: List[RegressionItem] = field(default_factory=list)
    open_questions: List[Question] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    environment_needs: List[str] = field(default_factory=list)
    exit_criteria: List[str] = field(default_factory=list)
    validation: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        raw = asdict(self)
        raw["schema_version"] = SCHEMA_VERSION
        return raw

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> TestPlan:
        """Build a plan from a dict that already passed ``schema_errors``."""
        return cls(
            meta=dict(raw["meta"]),
            summary=Summary(**raw["summary"]),
            risks=[
                PlanRisk(
                    id=r["id"],
                    title=r["title"],
                    likelihood=r["likelihood"],
                    impact=r["impact"],
                    adjustment=Adjustment(**r["adjustment"]),
                    evidence=list(r["evidence"]),
                )
                for r in raw["risks"]
            ],
            cases=[TestCase(**c) for c in raw["cases"]],
            regression_scope=[RegressionItem(**x) for x in raw["regression_scope"]],
            open_questions=[Question(**q) for q in raw["open_questions"]],
            assumptions=list(raw["assumptions"]),
            environment_needs=list(raw["environment_needs"]),
            exit_criteria=list(raw["exit_criteria"]),
            validation=[dict(v) for v in raw.get("validation", [])],
        )
