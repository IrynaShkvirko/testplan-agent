"""The context bundle: every fact the planner is allowed to reason from, as one JSON document."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from . import (
    __version__,
    classify,
    codeinfo,
    coverage,
    environment,
    gitsignals,
    importgraph,
    risk,
    surface,
    testscan,
)
from . import redact as redaction
from . import requirements as req
from .diffparse import FileChange, parse_diff
from .facts import FactStore
from .risk import RiskItem

BUNDLE_VERSION = 1
DEFAULT_CONTEXT_CHARS = 60_000

_KIND_RANK = {"source": 0, "migration": 1, "config": 2, "dependency": 2, "ci": 3, "test": 4}
_STOP = set(
    "the a an and or of to in on for with by is are be been being as at from that this it its "
    "must shall should will can may not no yes when then given if than into per each any all "
    "user users system only also more most less least than over under above below same "
    "have has had was were does did done use used using new old one two three".split()
)


@dataclass
class ContextBundle:
    meta: Dict[str, Any]
    title: str
    story: str
    change_class: Dict[str, Any]
    changes: List[surface.ChangeSummary]
    requirements: List[req.Requirement]
    ambiguities: List[req.Ambiguity]
    existing_tests: List[testscan.ExistingTest]
    dependents: List[importgraph.Dependent]
    risks: List[RiskItem]
    environment: Dict[str, Any]
    facts: FactStore
    diff_excerpt: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # ---- lookups used by validators and planners --------------------------------------
    def criteria(self) -> List[req.Requirement]:
        return [r for r in self.requirements if r.kind == req.CRITERION]

    def risk_by_id(self) -> Dict[str, RiskItem]:
        return {r.id: r for r in self.risks}

    def change_by_path(self) -> Dict[str, surface.ChangeSummary]:
        return {c.path: c for c in self.changes}

    def existing_ids(self) -> Set[str]:
        return {t.nodeid for t in self.existing_tests}

    # ---- serialisation ----------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": BUNDLE_VERSION,
            "meta": self.meta,
            "title": self.title,
            "story": self.story,
            "change_class": self.change_class,
            "changes": [asdict(c) for c in self.changes],
            "requirements": [asdict(r) for r in self.requirements],
            "ambiguities": [asdict(a) for a in self.ambiguities],
            "existing_tests": [asdict(t) for t in self.existing_tests],
            "dependents": [asdict(d) for d in self.dependents],
            "risks": [risk.to_dict(r) for r in self.risks],
            "environment": self.environment,
            "facts": self.facts.to_list(),
            "diff_excerpt": self.diff_excerpt,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> ContextBundle:
        """Rebuild a saved bundle. Raises ValueError if it is not one this version can read."""
        if not isinstance(raw, dict):
            raise ValueError("a context bundle is a JSON object")
        if raw.get("version") != BUNDLE_VERSION:
            raise ValueError(f"unsupported context bundle version: {raw.get('version')!r}")
        try:
            return cls._from_dict(raw)
        except (TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"malformed context bundle: {exc}") from exc

    @classmethod
    def _from_dict(cls, raw: Dict[str, Any]) -> ContextBundle:

        def change(item: Dict[str, Any]) -> surface.ChangeSummary:
            data = dict(item)
            data["symbols"] = [surface.ChangedSymbol(**s) for s in data.get("symbols", [])]
            data["new_ranges"] = [tuple(r) for r in data.get("new_ranges", [])]
            return surface.ChangeSummary(**data)

        return cls(
            meta=dict(raw.get("meta", {})),
            title=raw.get("title", ""),
            story=raw.get("story", ""),
            change_class=dict(raw.get("change_class", {})),
            changes=[change(c) for c in raw.get("changes", [])],
            requirements=[req.Requirement(**r) for r in raw.get("requirements", [])],
            ambiguities=[req.Ambiguity(**a) for a in raw.get("ambiguities", [])],
            existing_tests=[testscan.ExistingTest(**t) for t in raw.get("existing_tests", [])],
            dependents=[importgraph.Dependent(**d) for d in raw.get("dependents", [])],
            risks=[risk.from_dict(r) for r in raw.get("risks", [])],
            environment=dict(raw.get("environment", {})),
            facts=FactStore.from_list(raw.get("facts", [])),
            diff_excerpt=list(raw.get("diff_excerpt", [])),
            warnings=list(raw.get("warnings", [])),
        )


# ---- helpers ------------------------------------------------------------------------------
def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def content_tokens(text: str) -> Set[str]:
    """Lowercase stems of the meaningful words in text, splitting identifiers."""
    tokens: Set[str] = set()
    for raw in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        for part in codeinfo.split_identifier(raw):
            if len(part) >= 4 and part not in _STOP:
                tokens.add(_stem(part))
    return tokens


def render_patch(change: FileChange) -> str:
    out = [f"--- {change.old_path or change.path}", f"+++ {change.path}"]
    for h in change.hunks:
        out.append(
            f"@@ -{h.old_start},{h.old_len} +{h.new_start},{h.new_len} @@ {h.section}".rstrip()
        )
        out.extend(f"{m}{t}" for m, t in h.lines)
    return "\n".join(out)


def _excerpt(
    changes: Sequence[FileChange], summaries: Sequence[surface.ChangeSummary], budget: int
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Patches ranked by risk, cut to a character budget. Dropped files are named, not hidden."""
    order = sorted(
        range(len(changes)),
        key=lambda i: (
            _KIND_RANK.get(summaries[i].kind, 5),
            -(summaries[i].added + summaries[i].removed),
        ),
    )
    out: List[Dict[str, Any]] = []
    notes: List[str] = []
    used = 0
    for i in order:
        text = render_patch(changes[i])
        if changes[i].binary:
            text = f"(binary file {changes[i].path})"
        left = budget - used
        if left <= 200:
            notes.append(
                f"diff of {changes[i].path} omitted: context budget of {budget} characters used"
            )
            continue
        if len(text) > left:
            marker = "\n... [truncated]"
            text = text[: left - len(marker)] + marker
            notes.append(
                f"diff of {changes[i].path} truncated to fit the {budget} character budget"
            )
        used += len(text)
        out.append({"path": changes[i].path, "patch": text})
    return out, notes


def _symbol_text(sym: surface.ChangedSymbol) -> str:
    where = f"{sym.path}:{sym.start}-{sym.end}" if sym.start else sym.path
    label = "endpoint " + sym.endpoint if sym.endpoint else sym.kind
    extra = f"; signature {sym.signature_change}" if sym.signature_change else ""
    return f"{label} {sym.qualname} {sym.status} at {where}{extra}"


def build_bundle(
    diff_text: str,
    story_text: str = "",
    repo: Optional[Path] = None,
    as_of: Optional[date] = None,
    title: Optional[str] = None,
    quarantine_path: Optional[Path] = None,
    coverage_path: Optional[Path] = None,
    constraints: Optional[Dict[str, Any]] = None,
    max_context_chars: int = DEFAULT_CONTEXT_CHARS,
) -> ContextBundle:
    """Collect every fact about a change. Raises DiffError if the diff cannot be read."""
    as_of = as_of or date.today()
    repo = repo.resolve() if repo is not None else None
    if repo is not None and not repo.is_dir():
        raise FileNotFoundError(f"repository path is not a directory: {repo}")
    facts = FactStore()
    warnings: List[str] = []

    clean_diff, diff_hits = redaction.redact(diff_text)
    clean_story, story_hits = redaction.redact(story_text)
    hits = diff_hits + story_hits
    changes = parse_diff(clean_diff)
    story = req.parse_story(clean_story)
    summaries = [surface.summarize(c, repo) for c in changes]

    if hits:
        summary = ", ".join(f"{n} {k}" for k, n in sorted(hits.items()))
        facts.add(
            "redaction", f"redacted before analysis: {summary}", "redactor", counts=dict(hits)
        )

    # -- change surface
    path_fact: Dict[str, str] = {}
    for c in summaries:
        fact = facts.add(
            "change_file",
            f"{c.kind} file {c.path} {c.status} (+{c.added}/-{c.removed})",
            "diff",
            path=c.path,
            file_kind=c.kind,
            status=c.status,
            added=c.added,
            removed=c.removed,
            old_path=c.old_path,
        )
        path_fact[c.path] = fact.id
    changed_symbols = [
        (c, s) for c in summaries if c.kind == "source" for s in c.symbols if s.kind != "module"
    ]
    for _summary, s in changed_symbols:
        facts.add(
            "symbol",
            _symbol_text(s),
            "diff",
            qualname=s.qualname,
            path=s.path,
            sym_kind=s.kind,
            status=s.status,
            start=s.start,
            end=s.end,
            endpoint=s.endpoint,
            signature_change=s.signature_change,
        )
    area_paths: Dict[str, Dict[str, Any]] = {}
    for c in summaries:
        for area, terms in c.areas.items():
            entry = area_paths.setdefault(area, {"terms": [], "paths": []})
            entry["paths"].append(c.path)
            entry["terms"].extend(t for t in terms if t not in entry["terms"])
    for area, entry in sorted(area_paths.items()):
        label = surface.AREAS[area][0]
        facts.add(
            "sensitive_area",
            f"{label} touched in {', '.join(entry['paths'][:3])} (terms: {', '.join(entry['terms'][:5])})",
            "diff",
            area=area,
            terms=entry["terms"][:8],
            paths=entry["paths"],
        )

    # -- requirements
    for r in story.requirements:
        kind = "requirement" if r.kind == req.CRITERION else "non_goal"
        r.fact_id = facts.add(kind, f"{r.id}: {r.text}", "story", id=r.id, line=r.line).id
    for a in story.ambiguities:
        a.fact_id = facts.add(
            "ambiguity", a.text, "story", requirement=a.requirement_id, term=a.term
        ).id

    # -- existing tests, dependents, history, coverage, environment
    existing: List[testscan.ExistingTest] = []
    dependents: List[importgraph.Dependent] = []
    env: Dict[str, Any] = {}
    if repo is None:
        warnings.append(
            "no repository given: existing tests, dependents and history were not analysed"
        )
        facts.add("warning", warnings[-1], "collector")
    else:
        quarantine_file = quarantine_path or (repo / "quarantine.json")
        quarantined = testscan.load_quarantine(quarantine_file)
        files = testscan.scan_test_files(repo)
        existing = testscan.match_existing(summaries, files, quarantined)
        for t in existing[:40]:
            facts.add(
                "existing_test",
                f"{t.nodeid} ({t.strength}): {'; '.join(t.reasons[:2])}",
                "repo",
                nodeid=t.nodeid,
                strength=t.strength,
                symbols=t.symbols,
                quarantined=t.quarantined,
            )
        direct_names = {n for t in existing if t.strength == testscan.DIRECT for n in t.symbols}
        for _summary, s in changed_symbols:
            simple = s.qualname.rsplit(".", 1)[-1]
            if _has_changed_members(s, changed_symbols):
                continue
            if s.status != "removed" and simple not in direct_names and not simple.startswith("_"):
                facts.add(
                    "untested_symbol",
                    f"no existing test calls {s.qualname} ({s.path})",
                    "repo",
                    qualname=s.qualname,
                    path=s.path,
                )

        graph = importgraph.build_graph(repo)
        dependents = importgraph.dependents(summaries, graph)
        for d in dependents[:30]:
            facts.add(
                "dependent",
                f"{d.path} imports {d.via} ({d.hops} hop{'s' if d.hops > 1 else ''} from the change)",
                "repo",
                module=d.module,
                path=d.path,
                hops=d.hops,
                via=d.via,
            )

        if gitsignals.is_git_repo(repo):
            # A renamed file has its history under the old name while the checkout is at the base.
            asked = {
                (c.old_path if c.old_path and not (repo / c.path).exists() else c.path): c.path
                for c in summaries
                if c.kind == "source" and c.status != "added"
            }
            for queried, hist in gitsignals.file_history(repo, list(asked), as_of).items():
                path = asked[queried]
                if hist.last_change is None:
                    continue
                facts.add(
                    "git_history",
                    f"{path}: {_plural(hist.commits_90d, 'commit')} by "
                    f"{_plural(hist.authors_90d, 'author')} in {gitsignals.CHURN_DAYS} days, "
                    f"{_plural(hist.bugfixes_365d, 'bug-fix commit')} in {gitsignals.BUGFIX_DAYS} days",
                    "git",
                    path=path,
                    commits_90d=hist.commits_90d,
                    authors_90d=hist.authors_90d,
                    bugfixes_365d=hist.bugfixes_365d,
                    bugfix_subjects=hist.bugfix_subjects,
                    last_change=hist.last_change,
                )
        else:
            warnings.append("the repository is not a git checkout: history signals were skipped")
            facts.add("warning", warnings[-1], "collector")

        env = environment.detect(repo)
        facts.add("environment", _env_text(env), "repo", **env)

    if coverage_path is not None:
        try:
            cov = coverage.load_cobertura(coverage_path)
        except coverage.CoverageError as exc:
            warnings.append(f"coverage data ignored: {exc}")
            facts.add("warning", warnings[-1], "collector")
        else:
            for c, chg in zip(summaries, changes, strict=True):
                if c.kind != "source":
                    continue
                gaps = coverage.uncovered(cov, c.path, set(chg.added_line_numbers()))
                if gaps:
                    lines = sorted(gaps)
                    facts.add(
                        "uncovered_lines",
                        f"{c.path}: {len(lines)} changed line(s) never executed by the suite "
                        f"(e.g. {', '.join(map(str, lines[:5]))})",
                        "coverage",
                        path=c.path,
                        lines=lines[:50],
                    )

    # -- requirement / code mapping
    _map_requirements(facts, story, summaries, changes, changed_symbols)

    # -- classification and risk
    cls, traits, reasons = classify.classify(
        summaries, title or story.title, clean_story, bool(story.criteria)
    )
    facts.add(
        "change_class",
        f"change class: {cls} ({'; '.join(reasons)})",
        "classifier",
        **{"class": cls, "traits": traits, "reasons": reasons},
    )
    risks = risk.assess(facts)

    excerpt, notes = _excerpt(changes, summaries, max_context_chars)
    for note in notes:
        warnings.append(note)
        facts.add("warning", note, "collector")

    meta = {
        "tool_version": __version__,
        "as_of": as_of.isoformat(),
        "diff_sha256": hashlib.sha256(clean_diff.encode("utf-8")).hexdigest(),
        "story_sha256": hashlib.sha256(clean_story.encode("utf-8")).hexdigest(),
        "repo_provided": repo is not None,
        "redactions": dict(hits),
        "constraints": dict(constraints or {}),
        "context_char_budget": max_context_chars,
    }
    return ContextBundle(
        meta=meta,
        title=title or story.title or "Untitled change",
        story=clean_story,
        change_class={"class": cls, "traits": traits, "reasons": reasons},
        changes=summaries,
        requirements=story.requirements,
        ambiguities=story.ambiguities,
        existing_tests=existing,
        dependents=dependents,
        risks=risks,
        environment=env,
        facts=facts,
        diff_excerpt=excerpt,
        warnings=warnings,
    )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _has_changed_members(
    sym: surface.ChangedSymbol,
    changed_symbols: Sequence[Tuple[surface.ChangeSummary, surface.ChangedSymbol]],
) -> bool:
    """True for a class whose own methods are also in the change: the methods are the units to
    test, so the class itself is not listed separately."""
    prefix = sym.qualname + "."
    return any(o.path == sym.path and o.qualname.startswith(prefix) for _, o in changed_symbols)


def classify_path_is_source(path: str) -> bool:
    return surface.classify_path(path) in ("source", "migration", "config")


def _env_text(env: Dict[str, Any]) -> str:
    parts = []
    if env.get("frameworks"):
        parts.append("frameworks: " + ", ".join(env["frameworks"]))
    if env.get("ci"):
        parts.append(f"{len(env['ci'])} CI file(s)")
    if env.get("fixtures"):
        parts.append(f"{len(env['fixtures'])} fixture(s)")
    return "test environment: " + ("; ".join(parts) or "nothing detected")


def _map_requirements(
    facts: FactStore,
    story: req.Story,
    summaries: Sequence[surface.ChangeSummary],
    changes: Sequence[FileChange],
    changed_symbols: Sequence[Tuple[surface.ChangeSummary, surface.ChangedSymbol]],
) -> None:
    """Flag criteria with no matching code and public code with no matching criterion.

    Token overlap is a heuristic, so both outcomes are questions for a human, never verdicts.
    """
    source = [c for c in summaries if c.kind in ("source", "migration", "config")]
    if not story.criteria or not source:
        return
    vocab: Set[str] = set()
    for c in source:
        vocab |= content_tokens(c.path.replace("/", " "))
        for s in c.symbols:
            vocab |= content_tokens(s.qualname)
    for chg in changes:
        if classify_path_is_source(chg.path):
            vocab |= content_tokens(chg.hunk_text())
    for r in story.criteria:
        wanted = content_tokens(r.text)
        needed = 1 if len(wanted) < 3 else 2
        if len(wanted & vocab) < needed:
            facts.add(
                "unmapped_requirement",
                f"{r.id} shares almost no wording with the changed code; it may already hold, "
                "live elsewhere or be missing, so confirm where it is built",
                "mapper",
                requirement=r.id,
            )
    req_tokens: Set[str] = set()
    for r in story.criteria:
        req_tokens |= content_tokens(r.text)
    reported = 0
    for _summary, s in changed_symbols:
        simple = s.qualname.rsplit(".", 1)[-1]
        if simple.startswith("_") or s.status == "removed":
            continue
        if _has_changed_members(s, changed_symbols):
            continue
        if not (content_tokens(s.qualname) & req_tokens) and reported < 10:
            reported += 1
            facts.add(
                "unmapped_change",
                f"{s.qualname} ({s.path}) changed but no criterion mentions it; confirm it is in scope",
                "mapper",
                qualname=s.qualname,
                path=s.path,
            )
