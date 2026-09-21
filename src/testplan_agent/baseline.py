"""A rule-based planner: turns a context bundle into a plan without any model.

It is deliberately plain. Its jobs are to make the tool useful offline, to give the validators
something realistic to check, and to be the baseline that a real model must beat in evaluation.
Every case it writes cites facts from the bundle; the templates are labelled as templates.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .bundle import ContextBundle, content_tokens
from .facts import Fact
from .risk import RiskItem
from .schema import SCHEMA_VERSION

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_BOUNDARY = re.compile(
    r"\b(at most|at least|maximum|minimum|max|min|limit|cap|capped|no more than|no less than|"
    r"more than|less than|above|below|exceed|exceeds|up to|within|older than|under|over)\b",
    re.IGNORECASE,
)
_NEGATIVE = re.compile(
    r"\b(not|never|reject|rejected|rejects|invalid|error|fail|fails|cannot|denied|forbidden|"
    r"unauthori[sz]ed|refuse|refused)\b",
    re.IGNORECASE,
)
_TIME = re.compile(
    r"\b(expire|expires|expiry|expired|timeout|after \d+|minutes?|seconds?|hours?|days?)\b", re.I
)
_PERMISSION = re.compile(
    r"\b(permission|role|admin|authori[sz]ed|forbidden|denied|logged in|authenticated|only .{0,20} can)\b",
    re.IGNORECASE,
)
_CONCURRENT = re.compile(
    r"\b(concurrent|simultaneous|same time|race|parallel|two (users|requests))\b", re.I
)
_REPEAT = re.compile(
    r"\b(retry|retried|retries|twice|duplicate|duplicated|more than once|only once|recorded once|"
    r"idempotent|double[- ]submit)\b",
    re.IGNORECASE,
)
_ENDPOINT_WORDS = re.compile(
    r"\b(endpoint|request|response|status|http|api|payload)\b", re.IGNORECASE
)
_WRITE_VERBS = ("POST", "PUT", "PATCH", "DELETE")

PRIORITY = {"high": "P0", "medium": "P1", "low": "P2"}
_STEP_LIMIT = 90


def _short(text: str, limit: int = _STEP_LIMIT) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


class _Builder:
    def __init__(self, bundle: ContextBundle) -> None:
        self.b = bundle
        self.cases: List[Dict[str, Any]] = []
        levels = (bundle.meta.get("constraints") or {}).get("levels")
        self.levels: List[str] = [x for x in (levels or []) if isinstance(x, str)]
        self.risks = self._choose_risks()
        self.risk_tokens: Dict[str, Set[str]] = {}
        for r in self.risks:
            tokens = content_tokens(r.title)
            for fid in r.evidence:
                fact = bundle.facts.get(fid)
                if fact:
                    tokens |= content_tokens(fact.text)
            self.risk_tokens[r.id] = tokens
        # files each area risk is about, and how many conditions each risk has been given so far
        self.risk_paths: Dict[str, Set[str]] = {}
        self.risk_count: Dict[str, int] = {r.id: 0 for r in self.risks}
        for r in self.risks:
            self.risk_paths[r.id] = set()
            for fid in r.evidence:
                fact = bundle.facts.get(fid)
                if fact and fact.kind == "sensitive_area":
                    self.risk_paths[r.id] |= {str(x) for x in fact.data.get("paths", [])}
        self.symbols: List[Fact] = [
            f for f in bundle.facts.by_kind("symbol") if f.data.get("status") != "removed"
        ]
        self.existing_fact = {f.data["nodeid"]: f.id for f in bundle.facts.by_kind("existing_test")}
        self.targeted: Set[str] = set()
        # (technique, path) pairs already covered by a criterion, so structural cases do not repeat them
        self.covered: Set[Tuple[str, str]] = set()

    def _mark(self, technique: str, syms: List[Fact]) -> None:
        for f in syms:
            self.covered.add((technique, str(f.data["path"])))
        if not syms:
            self.covered.add((technique, "*"))

    def _is_covered(self, technique: str, path: str) -> bool:
        return (technique, path) in self.covered or (technique, "*") in self.covered

    # ---- helpers ----------------------------------------------------------------------
    def _choose_risks(self) -> List[RiskItem]:
        ranked = list(self.b.risks)
        keep = [r for r in ranked if r.level == "high"]
        for r in ranked:
            if r not in keep and len(keep) < 10:
                keep.append(r)
        return sorted(keep, key=lambda r: int(r.id[1:]))

    def pick_risk(
        self, tokens: Set[str], kind: Optional[str] = None, paths: Optional[Set[str]] = None
    ) -> Optional[RiskItem]:
        if not self.risks:
            return None
        if paths:
            # Prefer a sensitive-area risk about the same files; among those, the one whose
            # wording is closest, then the highest score, then the one with fewest conditions.
            near = [
                r
                for r in self.risks
                if r.kind.startswith("area:") and self.risk_paths[r.id] & paths
            ]
            if near:
                return min(
                    near,
                    key=lambda r: (
                        -len(tokens & self.risk_tokens[r.id]),
                        -r.score,
                        self.risk_count[r.id],
                        int(r.id[1:]),
                    ),
                )
        if kind:
            for r in self.risks:
                if r.kind == kind or r.kind.startswith(kind):
                    return r
        best, best_key = self.risks[0], (-1, -1)
        for r in self.risks:
            key = (len(tokens & self.risk_tokens[r.id]), r.score)
            if key > best_key:
                best, best_key = r, key
        return best

    def level_for(self, wanted: str) -> str:
        return wanted if not self.levels or wanted in self.levels else self.levels[0]

    def linked_symbols(self, tokens: Set[str]) -> List[Fact]:
        hits = [
            f
            for f in self.symbols
            if content_tokens(f"{f.data['qualname']} {f.data['path']}") & tokens
        ]
        return hits or []

    def evidence_for(self, first: List[str], syms: List[Fact]) -> List[str]:
        out = [x for x in first if x]
        for f in syms[:2]:
            out.append(f.id)
        for f in syms[:1]:
            start = int(f.data.get("start") or 0)
            change = self.b.change_by_path().get(str(f.data["path"]))
            if start >= 1 and change is not None:
                if change.line_count is None or start <= change.line_count:
                    out.append(f"{f.data['path']}:{start}")
        if not out:
            files = self.b.facts.by_kind("change_file")
            if files:
                out.append(files[0].id)
        return list(dict.fromkeys(out))

    def existing_for(self, syms: List[Fact], tokens: Set[str], min_overlap: int = 3) -> str:
        """An existing test that seems to check this: it calls the changed code and its name
        shares at least ``min_overlap`` meaningful words with the requirement. Names are a weak
        signal, so the bar is deliberately high and the plan marks the result as a match by name.
        """
        names = {str(f.data["qualname"]).rsplit(".", 1)[-1] for f in syms}
        best, best_score = "none", -1
        for t in self.b.existing_tests:
            if t.strength != "direct" or t.quarantined or not names & set(t.symbols):
                continue
            score = len(content_tokens(t.nodeid.rsplit("::", 1)[-1]) & tokens)
            if score >= min_overlap and score > best_score:
                best, best_score = t.nodeid, score
        return best

    def add(
        self,
        title: str,
        requirement: str,
        evidence: List[str],
        technique: str,
        level: str,
        risk: Optional[RiskItem],
        steps: List[str],
        expected: str,
        reason: str = "",
        existing: str = "none",
        confidence: str = "medium",
        confidence_reason: str = "rule-based template; a reviewer must supply concrete test data",
    ) -> None:
        if risk is None:
            return
        priority = PRIORITY[risk.level]
        self.risk_count[risk.id] = self.risk_count.get(risk.id, 0) + 1
        level = self.level_for(level)
        automation = "manual" if level == "exploratory" else ("later" if level == "e2e" else "now")
        self.cases.append(
            {
                "id": "",
                "title": _short(title, 110),
                "requirement": requirement,
                "requirement_reason": reason,
                "evidence": evidence,
                "technique": technique,
                "level": level,
                "priority": priority,
                "risk": risk.id,
                "automation": automation,
                "existing_coverage": existing,
                "steps": steps if priority in ("P0", "P1") else [],
                "expected": expected if priority in ("P0", "P1") else "",
                "confidence": confidence,
                "confidence_reason": confidence_reason if confidence != "high" else "",
                "_risk_score": risk.score,
            }
        )

    # ---- cases from acceptance criteria -----------------------------------------------
    def criterion_cases(self) -> None:
        ambiguous: Dict[str, List[str]] = {}
        for a in self.b.ambiguities:
            ambiguous.setdefault(a.requirement_id, []).append(a.term)
        for r in self.b.criteria():
            tokens = content_tokens(r.text)
            syms = self.linked_symbols(tokens)
            for f in syms:
                self.targeted.add(f"{f.data['path']}::{f.data['qualname']}")
            risk_tokens = tokens | {t for f in syms for t in content_tokens(str(f.data["path"]))}
            risk = self.pick_risk(risk_tokens, paths={str(f.data["path"]) for f in syms})
            base = [r.fact_id or ""]
            evidence = self.evidence_for(base, syms)
            target = syms[0].data["qualname"] if syms else "the changed behaviour"
            where = f" ({syms[0].data['path']})" if syms else ""
            is_endpoint = any(f.data.get("endpoint") for f in syms) or bool(
                _ENDPOINT_WORDS.search(r.text)
            )
            main_level = "integration" if is_endpoint else ("unit" if syms else "integration")
            existing = self.existing_for(syms, tokens)
            conf, why = "medium", "rule-based template; a reviewer must supply concrete test data"
            if r.id in ambiguous:
                conf = "low"
                why = f"the criterion uses vague wording ({', '.join(ambiguous[r.id])}); the expected result is one reading"

            self.add(
                f"{r.id}: {_short(r.text)}",
                r.id,
                evidence,
                "equivalence",
                main_level,
                risk,
                [
                    f"Arrange the state that {r.id} describes",
                    f"Exercise {target}{where}",
                    "Compare the outcome with the expected result",
                ],
                r.text,
                existing=existing,
                confidence=conf,
                confidence_reason=why,
            )
            numbers = _NUMBER.findall(r.text)
            if numbers and _BOUNDARY.search(r.text):
                n = numbers[0]
                self.add(
                    f"{r.id}: boundary at {n}",
                    r.id,
                    evidence,
                    "boundary",
                    "unit" if syms and not is_endpoint else main_level,
                    risk,
                    [
                        f"Run with a value just below {n}",
                        f"Run with a value exactly at {n}",
                        f"Run with a value just above {n}",
                    ],
                    f"The behaviour changes exactly where {r.id} says it does",
                    confidence=conf,
                    confidence_reason=why,
                )
            if _NEGATIVE.search(r.text):
                self.add(
                    f"{r.id}: the excluded case is refused",
                    r.id,
                    evidence,
                    "negative",
                    main_level,
                    risk,
                    [f"Provide input that {r.id} rules out", f"Exercise {target}{where}"],
                    "The input is rejected or handled as the criterion states, with no side effects",
                    confidence=conf,
                    confidence_reason=why,
                )
            if _TIME.search(r.text):
                self.add(
                    f"{r.id}: behaviour around the time limit",
                    r.id,
                    evidence,
                    "state_transition",
                    "integration",
                    self.pick_risk(set(), "area:time") or risk,
                    [
                        "Freeze or control the clock",
                        "Check the state just before the limit, at it, and just after it",
                    ],
                    "The state changes only once the limit has passed",
                    confidence=conf,
                    confidence_reason=why,
                )
            if _PERMISSION.search(r.text):
                self._mark("permission", syms)
                self.add(
                    f"{r.id}: caller without the right role",
                    r.id,
                    evidence,
                    "permission",
                    "integration",
                    self.pick_risk(set(), "area:auth") or risk,
                    ["Call as a user without the required role", "Call as a user with it"],
                    "Only the permitted user succeeds; the other is refused and nothing changes",
                    confidence=conf,
                    confidence_reason=why,
                )
            if _REPEAT.search(r.text):
                self._mark("idempotency", syms)
                self.add(
                    f"{r.id}: the same request repeated",
                    r.id,
                    evidence,
                    "idempotency",
                    "integration",
                    risk,
                    ["Perform the action", "Repeat it exactly, as a retry or double submit would"],
                    "The effect happens once; the repeat is rejected or returns the first result",
                    confidence=conf,
                    confidence_reason=why,
                )
            if _CONCURRENT.search(r.text):
                self.add(
                    f"{r.id}: two callers at the same time",
                    r.id,
                    evidence,
                    "concurrency",
                    "integration",
                    self.pick_risk(set(), "area:concurrency") or risk,
                    ["Start two callers together against the same record", "Check the final state"],
                    "No lost update, double booking or duplicate; totals stay consistent",
                    confidence=conf,
                    confidence_reason=why,
                )

    # ---- cases the criteria do not ask for ---------------------------------------------
    def structural_cases(self) -> None:
        facts = self.b.facts
        files = {f.data["path"]: f for f in facts.by_kind("change_file")}
        dependents = [f.id for f in facts.by_kind("dependent")][:3]

        for c in self.b.changes:
            if c.kind == "migration" and c.path in files:
                risk = self.pick_risk(set(), "area:data_loss") or self.pick_risk(set())
                self.add(
                    f"Migration {c.path} applies to empty and populated data and can be rolled back",
                    "none",
                    [files[c.path].id],
                    "migration",
                    "integration",
                    risk,
                    [
                        "Apply the migration to an empty database",
                        "Apply it to a copy of representative data and compare row counts",
                        "Roll it back and check the schema and data are unchanged",
                    ],
                    "No rows are lost or altered; the schema matches before and after a rollback",
                    reason="migrations are not described by the acceptance criteria",
                )

        for f in self.symbols:
            endpoint = f.data.get("endpoint")
            if endpoint and f.data.get("status") in ("added", "modified"):
                self.targeted.add(f"{f.data['path']}::{f.data['qualname']}")
                verb = str(endpoint).split(" ", 1)[0]
                risk = self.pick_risk(
                    content_tokens(f"{endpoint} {f.data['path']}"), paths={str(f.data["path"])}
                )
                ev = self.evidence_for([], [f])
                self.add(
                    f"{endpoint}: malformed or incomplete request",
                    "none",
                    ev,
                    "negative",
                    "integration",
                    self.pick_risk(set(), "contract") or risk,
                    ["Send a request with a missing field, a wrong type and an empty body"],
                    "A clear client error is returned and no state changes",
                    reason="input validation for a new or changed endpoint is not in the criteria",
                )
                path = str(f.data["path"])
                if verb in _WRITE_VERBS and not self._is_covered("permission", path):
                    self.add(
                        f"{endpoint}: caller who is not allowed to do this",
                        "none",
                        ev,
                        "permission",
                        "integration",
                        risk,
                        ["Call without credentials", "Call as a user without the required role"],
                        "Both calls are refused and nothing changes",
                        reason="authorisation for a write endpoint is not in the criteria",
                    )
                if verb in _WRITE_VERBS and not self._is_covered("idempotency", path):
                    self.add(
                        f"{endpoint}: the same request sent twice",
                        "none",
                        ev,
                        "idempotency",
                        "integration",
                        risk,
                        ["Send the request", "Send an identical request again"],
                        "The second call is rejected or returns the first result; nothing is done twice",
                        reason="retries and double submits are not in the criteria",
                    )

        for f in self.symbols:
            change = f.data.get("signature_change")
            if change:
                self.targeted.add(f"{f.data['path']}::{f.data['qualname']}")
                risk = self.pick_risk(set(), "contract")
                self.add(
                    f"Existing callers of {f.data['qualname']} still work after its signature changed",
                    "none",
                    self.evidence_for([], [f]) + dependents,
                    "compatibility",
                    "integration",
                    risk,
                    [
                        f"Find callers of {f.data['qualname']} (dependents are listed in the evidence)",
                        "Run them against the new signature, including default and keyword arguments",
                    ],
                    "Every caller works, or is updated in this change",
                    reason=f"signature changed {change}; criteria do not cover callers",
                )

        untested = {f.data["qualname"]: f for f in facts.by_kind("untested_symbol")}
        symbols_by_name = {str(f.data["qualname"]): f for f in self.symbols}
        for i, (qual, uf) in enumerate(sorted(untested.items())):
            sym = symbols_by_name.get(qual)
            key = f"{uf.data['path']}::{qual}"
            if i >= 5 or key in self.targeted:
                continue
            risk = self.pick_risk(content_tokens(qual), "untested")
            self.add(
                f"Characterise the current behaviour of {qual}",
                "none",
                self.evidence_for([uf.id], [sym] if sym else []),
                "regression",
                "unit",
                risk,
                [f"Call {qual} with typical, empty and extreme inputs", "Record what it returns"],
                "Results match what the author intended; surprises become questions",
                reason="no criterion mentions this change and no existing test calls it",
            )

        for f in facts.by_kind("uncovered_lines"):
            risk = self.pick_risk(content_tokens(str(f.data["path"])))
            self.add(
                f"Execute the changed lines in {f.data['path']} that no test reaches",
                "none",
                [f.id],
                "regression",
                "unit",
                risk,
                [f"Write inputs that reach lines {', '.join(map(str, f.data['lines'][:5]))}"],
                "The lines run and their result is asserted",
                reason="coverage data shows these changed lines are never executed",
            )

        if not self.b.criteria():
            for f in self.symbols[:8]:
                key = f"{f.data['path']}::{f.data['qualname']}"
                if key in self.targeted or f.data["sym_kind"] == "class":
                    continue
                self.targeted.add(key)
                risk = self.pick_risk(content_tokens(f"{f.data['qualname']} {f.data['path']}"))
                self.add(
                    f"Verify {f.data['qualname']} behaves as intended",
                    "none",
                    self.evidence_for([], [f]),
                    "equivalence",
                    "unit",
                    risk,
                    [
                        f"Call {f.data['qualname']} with typical and edge inputs",
                        "Assert on the result",
                    ],
                    "Behaviour matches the author's intent",
                    reason="no acceptance criteria were supplied",
                    existing=self.existing_for([f], set(), min_overlap=0),
                    confidence="low",
                    confidence_reason="there is no stated requirement to check against",
                )

    # ---- the rest of the plan --------------------------------------------------------------
    def finish(self) -> Dict[str, Any]:
        b = self.b
        order = {"P0": 0, "P1": 1, "P2": 2}
        indexed = list(enumerate(self.cases))
        indexed.sort(key=lambda t: (order[t[1]["priority"]], -t[1]["_risk_score"], t[0]))
        cases: List[Dict[str, Any]] = []
        for n, (_, case) in enumerate(indexed, start=1):
            case = dict(case)
            case.pop("_risk_score", None)
            case["id"] = f"TC-{n}"
            cases.append(case)

        criteria = b.criteria()
        top = b.risks[0] if b.risks else None
        cls = b.change_class.get("class", "change")
        text = f"{b.title}. {len(b.changes)} file(s) changed, classified as {cls}. "
        text += f"{len(criteria)} acceptance criteria found."
        if top:
            text += f" Highest risk: {top.title} (score {top.score}, {top.level})."
        scope = [f"{r.id}: {_short(r.text, 140)}" for r in criteria[:12]]
        if not scope:
            scope = [f"Changed file {c.path}" for c in b.changes[:12]]

        env = b.environment or {}
        assumptions: List[str] = []
        if env.get("frameworks"):
            assumptions.append("Tests are written with " + ", ".join(env["frameworks"]) + ".")
        else:
            assumptions.append("The test framework is unknown, so steps are framework-neutral.")
        assumptions.append(
            "The repository checkout matches the diff, either before or after the change."
        )
        redactions = b.facts.by_kind("redaction")
        if redactions:
            assumptions.append(
                f"Some values were redacted before analysis ({redactions[0].id}); tests that need them need real test data."
            )
        quarantined = [t.nodeid for t in b.existing_tests if t.quarantined]
        if quarantined:
            assumptions.append(
                f"{len(quarantined)} related test(s) are quarantined as flaky and are not counted as coverage."
            )

        needs: List[str] = []
        if env.get("fixtures"):
            needs.append(
                "Existing fixtures that may be reused: " + ", ".join(env["fixtures"][:8]) + "."
            )
        traits = set(b.change_class.get("traits", []))
        area_kinds = {r.kind for r in b.risks}
        if "migration" in traits:
            needs.append("A database with representative data, and a copy to roll back on.")
        if traits & {"new_endpoint", "changed_endpoint"}:
            needs.append("A test client or running app, with users of each relevant role.")
        if "area:time" in area_kinds:
            needs.append("A way to control the clock.")
        if "area:concurrency" in area_kinds:
            needs.append("Two or more concurrent workers against the same data.")
        if env.get("ci"):
            needs.append(
                "Cases marked 'now' should run in the existing CI: "
                + ", ".join(env["ci"][:2])
                + "."
            )
        if not needs:
            needs.append("No special environment beyond the existing test setup.")

        exit_criteria = [
            "All P0 and P1 cases have been run and pass.",
            "Every open question has an answer, or an accepted risk written next to it.",
            "No failing test is quarantined to make this change pass.",
        ]
        uncovered = b.facts.by_kind("uncovered_lines")
        if uncovered:
            exit_criteria.append(
                "Changed lines listed in "
                + ", ".join(f.id for f in uncovered)
                + " are executed by a test."
            )

        return {
            "schema_version": SCHEMA_VERSION,
            "meta": {
                "generator": "heuristic-baseline",
                "model": "none (rule-based)",
                "created": str(b.meta.get("as_of", "")),
                "bundle_sha256": "",
            },
            "summary": {
                "change_class": cls,
                "text": text,
                "in_scope": scope,
                "out_of_scope": [r.text for r in b.requirements if r.kind == "non_goal"][:8],
            },
            "risks": [
                {
                    "id": r.id,
                    "title": r.title,
                    "likelihood": r.likelihood,
                    "impact": r.impact,
                    "adjustment": {"likelihood_delta": 0, "impact_delta": 0, "reason": ""},
                    "evidence": r.evidence[:10],
                }
                for r in self.risks
            ],
            "cases": cases,
            "regression_scope": self.regression(),
            "open_questions": self.questions(),
            "assumptions": assumptions,
            "environment_needs": needs,
            "exit_criteria": exit_criteria,
        }

    def regression(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for t in self.b.existing_tests:
            if t.quarantined or t.nodeid not in self.existing_fact:
                continue
            out.append(
                {
                    "test": t.nodeid,
                    "reason": t.reasons[0] if t.reasons else "touches the changed module",
                    "evidence": [self.existing_fact[t.nodeid]],
                }
            )
            if len(out) >= 15:
                break
        return out

    def questions(self) -> List[Dict[str, Any]]:
        b = self.b
        items: List[Dict[str, Any]] = []

        def q(kind: str, text: str, evidence: List[str]) -> None:
            items.append(
                {"id": f"Q{len(items) + 1}", "kind": kind, "text": text, "evidence": evidence}
            )

        for f in b.facts.by_kind("ambiguity"):
            q(
                "ambiguity",
                f"{f.data['requirement']}: what does '{f.data['term']}' mean in practice? "
                "Give a number, a rule or an example so it can be tested.",
                [f.id],
            )
        for kind in ("unmapped_requirement", "unmapped_change"):
            for f in b.facts.by_kind(kind):
                q("unmapped", f.text[0].upper() + f.text[1:] + ".", [f.id])
        for f in b.facts.by_kind("warning"):
            q("missing_info", f.text[0].upper() + f.text[1:] + ".", [f.id])
        if not b.criteria():
            cls = b.facts.by_kind("change_class")
            q(
                "missing_info",
                "No acceptance criteria were found in the story. Add them, or this plan can only "
                "describe the change and not check it against a requirement.",
                [cls[0].id] if cls else [],
            )
        return items


def build_baseline_plan(bundle: ContextBundle) -> Dict[str, Any]:
    builder = _Builder(bundle)
    builder.criterion_cases()
    builder.structural_cases()
    return builder.finish()
