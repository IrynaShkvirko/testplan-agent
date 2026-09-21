"""The planning loop: prompt, parse, validate, repair (at most twice), then ship with flags."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import prompts
from .bundle import ContextBundle
from .llm import LLMClient, Turn, Usage
from .schema import TestPlan
from .validate import DEFAULT_MAX_CASES_PER_RISK, Issue, check_shape, has_errors, validate_plan

_FENCE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)


@dataclass
class PlanResult:
    plan: Optional[TestPlan]
    issues: List[Issue] = field(default_factory=list)
    attempts: int = 0
    raw: str = ""
    usage: List[Usage] = field(default_factory=list)  # one entry per call, in order

    @property
    def ok(self) -> bool:
        return self.plan is not None and not has_errors(self.issues)


def bundle_hash(bundle: ContextBundle) -> str:
    text = json.dumps(bundle.to_dict(), sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _error_count(issues: List[Issue]) -> int:
    return sum(i.severity == "error" for i in issues)


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
    turns: List[Turn] = [{"role": "user", "content": prompts.user_prompt(bundle)}]
    result = PlanResult(plan=None)
    best: Optional[Tuple[TestPlan, List[Issue]]] = None

    for attempt in range(max_repairs + 1):
        result.attempts = attempt + 1
        completion = client.complete(system, turns)
        result.usage.append(completion.usage)
        raw = completion.text
        result.raw = raw
        issues: List[Issue] = []
        plan: Optional[TestPlan] = None
        try:
            data = parse_model_json(raw)
        except ValueError as exc:
            issues.append(Issue("json", "error", "answer", f"the answer is not valid JSON: {exc}"))
            data = None
        if isinstance(data, dict):
            # Written by the tool, never taken from the answer.
            data["meta"] = {
                "generator": client.name,
                "model": client.model,
                "created": str(bundle.meta.get("as_of", "")),
                "bundle_sha256": bundle_hash(bundle),
            }
            data.pop("validation", None)
        if data is not None:
            issues.extend(check_shape(data))
            if not issues:
                plan = TestPlan.from_dict(data)
                issues.extend(validate_plan(plan, bundle, repo, max_cases_per_risk))
        if plan is not None and (best is None or _error_count(issues) <= _error_count(best[1])):
            best = (plan, issues)  # the well-formed plan with the fewest errors; later wins ties
        result.issues = issues
        if not has_errors(issues) or getattr(client, "deterministic", False):
            break  # clean, or asking again would only return the same answer
        if attempt < max_repairs:
            errors = [i for i in issues if i.severity == "error"]
            turns = turns + prompts.repair_turns(raw, errors)

    if best is not None:
        # A later answer that was not even valid JSON must not hide the plan we already have.
        result.plan, result.issues = best
        result.plan.validation = [i.to_dict() for i in result.issues]
        result.plan.meta["run"] = run_details(result.usage)
    return result


def run_details(usages: Sequence[Usage]) -> Dict[str, Any]:
    """Every call the plan took, and the totals. A total is unknown if any part of it is."""

    def total(key: str) -> Any:
        values = [getattr(u, key) for u in usages]
        if any(v is None for v in values):
            return None
        return round(sum(values), 6) if key == "cost_usd" else sum(values)

    keys = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
    return {
        "attempts": [{"attempt": n, **asdict(u)} for n, u in enumerate(usages, start=1)],
        "total": {key: total(key) for key in (*keys, "latency_ms", "cost_usd")},
    }
