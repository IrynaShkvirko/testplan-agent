"""Command line: ``testplan context | generate | validate | schema``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import __version__, prompts
from .bundle import DEFAULT_CONTEXT_CHARS, ContextBundle, build_bundle
from .diffparse import DiffError
from .llm import HeuristicClient, LLMClient, LLMError, ScriptedClient
from .planner import generate_plan, run_details
from .render import render_markdown, usage_summary
from .schema import PLAN_SCHEMA, TestPlan
from .validate import (
    DEFAULT_MAX_CASES_PER_RISK,
    Issue,
    check_shape,
    has_errors,
    validate_plan,
)


class CliError(Exception):
    pass


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(f"cannot read {path}: {exc}") from exc


def _write(text: str, output: Optional[str]) -> None:
    if not output:
        sys.stdout.write(text)
        return
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"wrote {output}", file=sys.stderr)


def _as_of(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CliError(f"--as-of must look like 2026-09-01, got {value!r}") from exc


def _constraints(args: argparse.Namespace) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if getattr(args, "levels", None):
        levels = [x.strip() for x in args.levels.split(",") if x.strip()]
        bad = [x for x in levels if x not in ("unit", "integration", "e2e", "exploratory")]
        if bad:
            raise CliError(f"unknown test level(s): {', '.join(bad)}")
        out["levels"] = levels
    if getattr(args, "budget", None):
        out["time_budget"] = args.budget
    return out


def _bundle_from_args(args: argparse.Namespace) -> ContextBundle:
    if getattr(args, "context", None):
        try:
            return ContextBundle.from_dict(json.loads(_read(args.context)))
        except ValueError as exc:
            raise CliError(f"{args.context} is not a usable context bundle: {exc}") from exc
    if not args.diff:
        raise CliError(
            "a diff is required: use --diff change.patch (or - for stdin), optionally with "
            "--spec story.md, or --context with a saved bundle"
        )
    diff_text = _read(args.diff)
    if not diff_text.strip():
        raise CliError(f"the diff in {args.diff} is empty")
    story = _read(args.spec) if args.spec else ""
    try:
        return build_bundle(
            diff_text,
            story,
            repo=Path(args.repo) if args.repo else None,
            as_of=_as_of(args.as_of),
            title=args.title,
            quarantine_path=Path(args.quarantine) if args.quarantine else None,
            coverage_path=Path(args.coverage) if args.coverage else None,
            constraints=_constraints(args),
            max_context_chars=args.max_context_chars,
        )
    except DiffError as exc:
        raise CliError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise CliError(str(exc)) from exc


def _client(args: argparse.Namespace) -> LLMClient:
    if args.client == "heuristic":
        return HeuristicClient()
    if not args.responses:
        raise CliError("--client scripted needs --responses FILE (a JSON object or list of them)")
    try:
        raw = json.loads(_read(args.responses))
    except ValueError as exc:
        raise CliError(f"{args.responses} is not valid JSON: {exc}") from exc
    return ScriptedClient(raw if isinstance(raw, list) else [raw])


# ---- commands -------------------------------------------------------------------------------
def cmd_context(args: argparse.Namespace) -> int:
    bundle = _bundle_from_args(args)
    _write(json.dumps(bundle.to_dict(), indent=2, sort_keys=True) + "\n", args.output)
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    bundle = _bundle_from_args(args)
    if args.save_context:
        _write(json.dumps(bundle.to_dict(), indent=2, sort_keys=True) + "\n", args.save_context)
    if args.show_context:
        sys.stdout.write("=== system ===\n" + prompts.system_prompt() + "\n\n")
        sys.stdout.write("=== user ===\n" + prompts.user_prompt(bundle) + "\n")
        return 0

    repo = Path(args.repo) if args.repo else None
    try:
        result = generate_plan(
            bundle,
            _client(args),
            repo=repo,
            max_repairs=args.max_repairs,
            max_cases_per_risk=args.max_cases_per_risk,
        )
    except LLMError as exc:
        raise CliError(f"the planner gave no answer: {exc}") from exc
    total = run_details(result.usage)["total"]
    if result.plan is None:
        for issue in result.issues:
            print(issue, file=sys.stderr)
        _print_usage(total)  # the calls were made, and paid for, even with nothing to show
        raise CliError(f"no usable plan after {result.attempts} attempt(s)")

    _write(render_markdown(result.plan, bundle), args.output)
    if args.json:
        _write(json.dumps(result.plan.to_dict(), indent=2, sort_keys=True) + "\n", args.json)
    errors = [i for i in result.issues if i.severity == "error"]
    warnings = [i for i in result.issues if i.severity != "error"]
    print(
        f"{len(result.plan.cases)} conditions, {len(result.plan.risks)} risks; "
        f"{len(errors)} error(s), {len(warnings)} warning(s) after {result.attempts} attempt(s)",
        file=sys.stderr,
    )
    _print_usage(total)
    for issue in errors:
        print(issue, file=sys.stderr)
    return 1 if errors else 0


def _print_usage(total: Dict[str, Any]) -> None:
    if total["input_tokens"] or total["output_tokens"]:
        print(f"model usage: {usage_summary(total)}", file=sys.stderr)


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        raw = json.loads(_read(args.plan))
    except ValueError as exc:
        raise CliError(f"{args.plan} is not valid JSON: {exc}") from exc
    try:
        bundle = ContextBundle.from_dict(json.loads(_read(args.context)))
    except ValueError as exc:
        raise CliError(f"{args.context} is not a usable context bundle: {exc}") from exc
    issues: List[Issue] = check_shape(raw)
    if not issues:
        issues = validate_plan(
            TestPlan.from_dict(raw),
            bundle,
            Path(args.repo) if args.repo else None,
            args.max_cases_per_risk,
        )
    for issue in issues:
        print(issue)
    print(
        "OK" if not has_errors(issues) else f"{sum(i.severity == 'error' for i in issues)} error(s)"
    )
    return 1 if has_errors(issues) else 0


def cmd_schema(args: argparse.Namespace) -> int:
    doc = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "testplan-agent plan",
        **PLAN_SCHEMA,
    }
    _write(json.dumps(doc, indent=2, sort_keys=True) + "\n", args.output)
    return 0


# ---- parsing --------------------------------------------------------------------------------
def _input_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--diff", metavar="FILE", help="unified diff of the change (- for stdin)")
    p.add_argument("--spec", metavar="FILE", help="story, issue or PR description (Markdown)")
    p.add_argument(
        "--repo", metavar="DIR", help="checkout of the repository, for tests and history"
    )
    p.add_argument("--as-of", metavar="DATE", help="date for history signals (default: today)")
    p.add_argument("--title", help="title of the change (default: first heading of the spec)")
    p.add_argument("--coverage", metavar="FILE", help="Cobertura XML from `coverage xml`")
    p.add_argument(
        "--quarantine", metavar="FILE", help="flaky-quarantine list (default: repo/quarantine.json)"
    )
    p.add_argument("--levels", metavar="LIST", help="test levels in scope, e.g. unit,integration")
    p.add_argument("--budget", metavar="TEXT", help='time available, e.g. "half a day"')
    p.add_argument(
        "--max-context-chars",
        type=int,
        default=DEFAULT_CONTEXT_CHARS,
        metavar="N",
        help="character budget for diff excerpts sent to the model",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="testplan",
        description="Turn a code change and its requirements into an evidence-grounded test plan.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("context", help="collect facts and write the context bundle as JSON")
    _input_args(p)
    p.add_argument("--output", "-o", metavar="FILE")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("generate", help="collect facts, draft a plan, validate it, render it")
    _input_args(p)
    p.add_argument(
        "--context", metavar="FILE", help="use a saved context bundle instead of collecting"
    )
    p.add_argument("--save-context", metavar="FILE", help="also write the context bundle here")
    p.add_argument(
        "--show-context",
        action="store_true",
        help="print exactly what would be sent to a model, then stop",
    )
    p.add_argument("--client", choices=("heuristic", "scripted"), default="heuristic")
    p.add_argument("--responses", metavar="FILE", help="canned answers for --client scripted")
    p.add_argument("--max-repairs", type=int, default=2, metavar="N")
    p.add_argument(
        "--max-cases-per-risk", type=int, default=DEFAULT_MAX_CASES_PER_RISK, metavar="N"
    )
    p.add_argument("--output", "-o", metavar="FILE", help="Markdown plan (default: stdout)")
    p.add_argument("--json", metavar="FILE", help="also write the plan as JSON")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("validate", help="check a plan JSON file against a context bundle")
    p.add_argument("plan", metavar="PLAN.json")
    p.add_argument("--context", required=True, metavar="FILE")
    p.add_argument("--repo", metavar="DIR")
    p.add_argument(
        "--max-cases-per-risk", type=int, default=DEFAULT_MAX_CASES_PER_RISK, metavar="N"
    )
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("schema", help="print the plan JSON Schema")
    p.add_argument("--output", "-o", metavar="FILE")
    p.set_defaults(func=cmd_schema)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
