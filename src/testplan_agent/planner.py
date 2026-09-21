"""The planning loop: prompt, parse, validate, repair (at most twice), then ship with flags."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from . import prompts
from .bundle import ContextBundle
from .llm import LLMClient
from .schema import TestPlan
from .validate import DEFAULT_MAX_CASES_PER_RISK, Issue, check_shape, has_errors, validate_plan

_FENCE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)


@dataclass
class PlanResult:
    plan: Optional[TestPlan]
    issues: List[Issue] = field(default_factory=list)
    attempts: int = 0
    raw: str = ""

    @property
    def ok(self) -> bool:
        return self.plan is not None and not has_errors(self.issues)


def bundle_hash(bundle: ContextBundle) -> str:
    text = json.dumps(bundle.to_dict(), sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_model_json(text: str) -> Any:
    """Parse a model answer: bare JSON, a fenced block, or JSON with chatter around it."""
    body = text.strip()
    fenced = _FENCE.match(body)
    if fenced:
        body = fenced.group(1)
    try:
        return json.loads(body)
    except ValueError:
        start, end = body.find("{"), body.rfind("}")
        if start >= 0 and end > start:
            return json.loads(body[start : end + 1])
        raise


def generate_plan(
    bundle: ContextBundle,
    client: LLMClient,
    repo: Optional[Path] = None,
    max_repairs: int = 2,
    max_cases_per_risk: int = DEFAULT_MAX_CASES_PER_RISK,
) -> PlanResult:
    system = prompts.system_prompt()
    original = prompts.user_prompt(bundle)
    user = original
    result = PlanResult(plan=None)
    best: Optional[tuple] = None

    for attempt in range(max_repairs + 1):
        result.attempts = attempt + 1
        raw = client.complete(system, user)
        result.raw = raw
        issues: List[Issue] = []
        plan: Optional[TestPlan] = None
        try:
            data = parse_model_json(raw)
        except ValueError as exc:
            issues.append(Issue("json", "error", "answer", f"the answer is not valid JSON: {exc}"))
            data = None
        if data is not None:
            issues.extend(check_shape(data))
            if not issues:
                plan = TestPlan.from_dict(data)
                plan.meta.update(
                    {
                        "generator": client.name,
                        "model": client.model,
                        "created": str(bundle.meta.get("as_of", "")),
                        "bundle_sha256": bundle_hash(bundle),
                    }
                )
                issues.extend(validate_plan(plan, bundle, repo, max_cases_per_risk))
        if plan is not None:
            best = (plan, issues)  # remember the last well-formed plan and what was wrong with it
        result.issues = issues
        if not has_errors(issues):
            break
        if attempt < max_repairs:
            user = prompts.repair_prompt(
                original, raw, [i for i in issues if i.severity == "error"]
            )

    if best is not None:
        # A later answer that was not even valid JSON must not hide the plan we already have.
        result.plan, result.issues = best
        result.plan.validation = [i.to_dict() for i in result.issues]
    return result
