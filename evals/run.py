"""Run the planner over the evaluation cases and record one graded row per case and rep.

    python -m evals.run --variant baseline --client heuristic
    python -m evals.run --variant v1 --client anthropic --reps 2      # costs money; asks first

Writes into ``<flow>/<variant>/`` (default flow: ``evals/runs``):

- ``results.jsonl``: one row per (case, rep), written as each finishes; a rerun skips rows
  that are already there, so an interrupted run resumes where it stopped.
- ``errors.jsonl``: attempts that produced nothing to grade (API error, timeout, a model other
  than the one asked for, a crash), with a failure class. They are never scored as a bad plan.
- ``traces/<case>_rep<k>.json``: the full conversation; ``plans/<case>_rep<k>.json``: the plan.

A refusal or an answer cut off at the token limit is a real outcome, not a harness error: it
gets a row with ``status`` "refused" or "truncated" and no scores, so it is counted but never
averaged in as a wrong answer.

Runs that call a paid model need a one-time ``--approve-harness`` (it records a hash of the
runner and the planner code, so a changed harness is noticed before its numbers are compared)
and a confirmation after a cost estimate, which ``--yes`` skips.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from testplan_agent import prompts
from testplan_agent.bundle import ContextBundle, build_bundle
from testplan_agent.llm import Completion, HeuristicClient, LLMClient, LLMError, Turn
from testplan_agent.planner import PlanResult, generate_plan
from testplan_agent.pricing import PRICES

from . import grading
from .cases import CASES_DIR, Case, CaseError, load_cases

ROOT = Path(__file__).resolve().parent.parent
FLOW_DIR = Path(__file__).resolve().parent / "runs"
PAID_CLIENTS = ("anthropic",)
# Code whose change changes the numbers; hashed for the harness approval.
HARNESS_PATHS = [
    "evals/run.py",
    "evals/cases.py",
    "evals/grading.py",
    "src/testplan_agent/prompts.py",
    "src/testplan_agent/planner.py",
    "src/testplan_agent/llm.py",
    "src/testplan_agent/anthropic_client.py",
    "src/testplan_agent/validate.py",
    "src/testplan_agent/schema.py",
    "src/testplan_agent/baseline.py",
]
DEFAULT_STATE: Dict[str, Any] = {
    "flow": "testplan",
    "metrics": grading.METRICS,
    "perf_fields": [
        {"id": "cost_usd", "label": "Cost", "unit": "$"},
        {"id": "latency_s", "label": "Model time", "unit": "s"},
        {"id": "attempts", "label": "Attempts"},
        {"id": "check_errors", "label": "Errors"},
        {"id": "check_warnings", "label": "Warnings"},
    ],
    "harness_paths": HARNESS_PATHS,
}


class RunError(Exception):
    """A reason not to run at all (exit code 2)."""


# ---- the client, recorded ---------------------------------------------------------------------
class RecordingClient:
    """Passes calls through and keeps each conversation and answer for the trace."""

    def __init__(self, inner: LLMClient) -> None:
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self.deterministic = getattr(inner, "deterministic", False)
        self.calls: List[Tuple[str, List[Turn], Optional[str]]] = []

    def complete(self, system: str, turns: Sequence[Turn]) -> Completion:
        entry: Tuple[str, List[Turn], Optional[str]] = (system, [dict(t) for t in turns], None)
        self.calls.append(entry)
        completion = self.inner.complete(system, turns)
        self.calls[-1] = (system, entry[1], completion.text)
        return completion

    def trace(self) -> List[Dict[str, str]]:
        if not self.calls:
            return []
        system, turns, answer = self.calls[-1]
        out = [{"role": "system", "content": system}]
        out += [{"role": t["role"], "content": t["content"]} for t in turns]
        if answer is not None:
            out.append({"role": "assistant", "content": answer})
        return out

    def first_answer(self) -> str:
        return (self.calls[0][2] or "") if self.calls else ""


# ---- state and the harness approval -----------------------------------------------------------
def harness_sha(paths: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(paths):
        path = ROOT / rel
        data = path.read_bytes() if path.is_file() else b"<missing>"
        digest.update(rel.encode() + b"\0" + hashlib.sha256(data).hexdigest().encode() + b"\n")
    return digest.hexdigest()


def load_state(flow: Path) -> Dict[str, Any]:
    path = flow / "_state.json"
    if not path.is_file():
        flow.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULT_STATE, indent=2) + "\n", encoding="utf-8")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RunError(f"{path} is not valid JSON: {exc}") from exc


def check_harness(flow: Path, state: Dict[str, Any], approve: bool) -> None:
    current = harness_sha(state.get("harness_paths") or HARNESS_PATHS)
    if approve:
        state["harness_sha"] = current
        (flow / "_state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        print(f"harness approved: {current[:12]}", file=sys.stderr)
        return
    recorded = state.get("harness_sha")
    if recorded != current:
        what = "has not been approved" if not recorded else "changed since it was approved"
        raise RunError(
            f"the harness {what}: review the runner and planner code, then rerun with "
            "--approve-harness (a paid run needs this once per harness version)"
        )


# ---- one case ---------------------------------------------------------------------------------
def estimate_cost(model: str, bundles: Dict[str, ContextBundle], reps: int) -> Optional[str]:
    """A rough range from prompt length: one clean attempt to three attempts at full length."""
    price = PRICES.get(model)
    if price is None:
        return None
    system = prompts.system_prompt()
    low = high = 0.0
    for bundle in bundles.values():
        tokens_in = (len(system) + len(prompts.user_prompt(bundle))) / 4
        low += (tokens_in * price.input + 4_000 * price.output) / 1e6
        high += (3 * tokens_in * price.input + 3 * 12_000 * price.output) / 1e6
    return f"${low * reps:.2f}-${high * reps:.2f}"


def run_case(
    case: Case,
    rep: int,
    bundle: ContextBundle,
    repo: Path,
    client: LLMClient,
    timeout_s: float,
) -> Dict[str, Any]:
    """Plan one case in a daemon thread under a hard wall-clock limit.

    Returns {"result", "recorder", "error", "wall_s"}. A call that outlives the limit keeps
    running in the background; the limit only frees this case's slot.
    """
    recorder = RecordingClient(client)
    box: Dict[str, Any] = {}

    def work() -> None:
        try:
            box["result"] = generate_plan(bundle, recorder, repo=repo)
        except BaseException as exc:  # noqa: BLE001 - everything is recorded, nothing escapes
            box["error"] = exc

    started = time.monotonic()
    thread = threading.Thread(target=work, name=f"{case.id}-rep{rep}", daemon=True)
    thread.start()
    thread.join(timeout_s)
    wall = round(time.monotonic() - started, 3)
    if thread.is_alive():
        return {"error": TimeoutError(f"no answer within {timeout_s:g} s"), "recorder": recorder,
                "wall_s": wall}  # fmt: skip
    return {**box, "recorder": recorder, "wall_s": wall}


def _usage_fields(total: Dict[str, Any]) -> Dict[str, int]:
    return {
        "input_tokens": total.get("input_tokens", 0),
        "output_tokens": total.get("output_tokens", 0),
        "cache_read_input_tokens": total.get("cache_read_tokens", 0),
        "cache_creation_input_tokens": total.get("cache_write_tokens", 0),
    }


def served_mismatch(result: PlanResult, requested: str) -> Optional[str]:
    """A model other than the one asked for answered (a fallback or a reroute)."""
    for usage in result.usage:
        if usage.model and not usage.model.startswith(requested):
            return usage.model
    return None


def record(
    case: Case,
    rep: int,
    outcome: Dict[str, Any],
    bundle: ContextBundle,
    repo: Path,
    client: LLMClient,
    vdir: Path,
) -> Tuple[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Turn an outcome into a results row or an errors row. Returns (kind, row, error)."""
    key = f"{case.id}_rep{rep}"
    recorder: RecordingClient = outcome["recorder"]
    trace = recorder.trace()
    if trace:
        _write_json(vdir / "traces" / f"{key}.json", trace)
    base = {"prompt_id": case.id, "rep": rep, "wall_s": outcome["wall_s"]}
    error = outcome.get("error")
    result: Optional[PlanResult] = outcome.get("result")

    if error is not None:
        if isinstance(error, LLMError) and error.kind in ("refusal", "truncated"):
            spent = error.spent or {}
            row = _row(case, rep, outcome["wall_s"], status="refused" if error.kind == "refusal"
                       else "truncated", model=client.model, total=spent, grade={})  # fmt: skip
            row["meta"]["reason"] = str(error)
            return "row", row, None
        if isinstance(error, LLMError):
            cls, spent = error.kind, error.spent or {}
        elif isinstance(error, TimeoutError):
            cls, spent = "timeout", {}
        else:
            cls, spent = "harness_error", {}
        detail = "".join(traceback.format_exception_only(type(error), error)).strip()
        err = {**base, "class": cls, "message": detail, "model": client.model}
        if spent:
            err["usage"] = _usage_fields(spent)
            err["cost_usd"] = spent.get("cost_usd")
        return "error", None, err

    assert result is not None
    if result.plan is None:
        issues = "; ".join(str(i) for i in result.issues[:3])
        err = {**base, "class": "no_plan", "message": issues, "model": client.model}
        err["usage"] = _usage_fields(_totals(result))
        return "error", None, err
    if client.name in PAID_CLIENTS:
        other = served_mismatch(result, client.model)
        if other:
            err = {**base, "class": "served_model_mismatch", "model": other,
                   "message": f"asked for {client.model}, answered by {other}"}  # fmt: skip
            err["usage"] = _usage_fields(_totals(result))
            return "error", None, err

    plan = result.plan
    _write_json(vdir / "plans" / f"{key}.json", plan.to_dict())
    total = plan.meta["run"]["total"]
    served = next((u.model for u in reversed(result.usage) if u.model), client.model)
    grade = grading.grade(plan, result.attempts, recorder.first_answer(), bundle, repo)
    row = _row(case, rep, outcome["wall_s"], status="ok", model=served, total=total, grade=grade)
    row["attempts"] = result.attempts
    row["check_errors"] = sum(v.get("severity") == "error" for v in plan.validation)
    row["check_warnings"] = sum(v.get("severity") != "error" for v in plan.validation)
    row["meta"]["plan"] = f"plans/{key}.json"
    last = result.usage[-1] if result.usage else None
    row["stop_reason"] = last.stop_reason if last else ""
    return "row", row, None


def _totals(result: PlanResult) -> Dict[str, Any]:
    from testplan_agent.planner import run_details

    return run_details(result.usage)["total"]


def _row(
    case: Case,
    rep: int,
    wall_s: float,
    status: str,
    model: str,
    total: Dict[str, Any],
    grade: Dict[str, float],
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "prompt_id": case.id,
        "rep": rep,
        "prompt": case.display(),
        "tags": case.tags,
        "status": status,
        "model": model,
        "grade": grade,
        "usage": _usage_fields(total),
        "wall_s": wall_s,
        "meta": {"defects": len(case.defects)},
    }
    if total.get("cost_usd") is not None:
        row["cost_usd"] = total["cost_usd"]
    if total.get("latency_ms") is not None:
        row["latency_s"] = round(total["latency_ms"] / 1000, 3)
    return row


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _done(vdir: Path) -> Set[Tuple[str, int]]:
    path = vdir / "results.jsonl"
    if not path.is_file():
        return set()
    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            done.add((row["prompt_id"], int(row["rep"])))
    return done


# ---- the summary ------------------------------------------------------------------------------
def summarize(vdir: Path) -> List[str]:
    rows = _read_jsonl(vdir / "results.jsonl")
    errors = _read_jsonl(vdir / "errors.jsonl")
    ok = [r for r in rows if r.get("status") == "ok"]
    lines = [
        f"{vdir.name}: {len(ok)} graded, "
        f"{sum(r.get('status') == 'refused' for r in rows)} refused, "
        f"{sum(r.get('status') == 'truncated' for r in rows)} truncated, "
        f"{len(errors)} failed attempt(s)"
    ]
    for metric in grading.METRICS:
        values = [r["grade"][metric["id"]] for r in ok if metric["id"] in r.get("grade", {})]
        if not values:
            continue
        mean = sum(values) / len(values)
        if metric["kind"] == "binary":
            half = 1.96 * math.sqrt(mean * (1 - mean) / len(values))
        else:
            sd = math.sqrt(sum((v - mean) ** 2 for v in values) / max(len(values) - 1, 1))
            half = 1.96 * sd / math.sqrt(len(values))
        lines.append(f"  {metric['label']:<14} {mean:.3f} ± {half:.3f}  (n={len(values)})")
    costs = [r["cost_usd"] for r in rows if isinstance(r.get("cost_usd"), (int, float))]
    costs += [e["cost_usd"] for e in errors if isinstance(e.get("cost_usd"), (int, float))]
    if costs:
        lines.append(f"  estimated cost  ${sum(costs):.2f} (list prices, not a bill)")
    return lines


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---- the command ------------------------------------------------------------------------------
def make_client(args: argparse.Namespace) -> LLMClient:
    if args.client == "heuristic":
        return HeuristicClient()
    from testplan_agent.anthropic_client import DEFAULT_EFFORT, DEFAULT_MODEL, AnthropicClient

    # No fallback: an answer from another model would be mixed into this variant's numbers.
    return AnthropicClient(
        model=args.model or DEFAULT_MODEL, effort=args.effort or DEFAULT_EFFORT, fallback=False
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals.run", description=__doc__.splitlines()[0])
    p.add_argument("--variant", required=True, help='"baseline" or v1, v2, ...')
    p.add_argument("--client", choices=("heuristic", "anthropic"), default="heuristic")
    p.add_argument("--model", help="for --client anthropic (default: claude-opus-5)")
    p.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"))
    p.add_argument("--reps", type=int, default=1, help="runs per case (default 1)")
    p.add_argument("--cases", type=Path, default=CASES_DIR, help="folder of case folders")
    p.add_argument("--only", nargs="*", metavar="ID", help="run just these cases")
    p.add_argument("--flow", type=Path, default=FLOW_DIR, help="where variants are written")
    p.add_argument("--timeout-s", type=float, default=900.0, help="hard limit per case")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--approve-harness", action="store_true", help="record the harness as approved")
    p.add_argument("--yes", action="store_true", help="skip the cost confirmation")
    return p


def main(
    argv: Optional[Sequence[str]] = None,
    client_factory: Callable[[argparse.Namespace], LLMClient] = make_client,
    confirm: Callable[[str], bool] = lambda text: input(text).strip().lower() in ("y", "yes"),
) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args, client_factory, confirm)
    except (RunError, CaseError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run(
    args: argparse.Namespace,
    client_factory: Callable[[argparse.Namespace], LLMClient],
    confirm: Callable[[str], bool],
) -> int:
    if not re.fullmatch(r"baseline|v\d+", args.variant):
        raise RunError('--variant must be "baseline" or v1, v2, ... (the report reads only these)')
    if args.client != "anthropic" and (args.model or args.effort):
        raise RunError("--model and --effort only apply with --client anthropic")
    cases = load_cases(args.cases, args.only)
    flow = args.flow
    state = load_state(flow)
    vdir = flow / args.variant
    client = client_factory(args)
    paid = client.name in PAID_CLIENTS
    reps = args.reps
    if getattr(client, "deterministic", False) and reps > 1:
        print(
            f"note: {client.name} gives the same answer every time; running 1 rep", file=sys.stderr
        )
        reps = 1
    if paid:
        check_harness(flow, state, args.approve_harness)
    elif args.approve_harness:
        check_harness(flow, state, True)
    if args.variant != "baseline" and not (vdir / "change.md").is_file():
        print(f"note: {vdir}/change.md is missing: the report will not say what changed",
              file=sys.stderr)  # fmt: skip

    workdir = Path(tempfile.mkdtemp(prefix="testplan-eval-"))
    try:
        repo, changes = _demo_repo(workdir)
        bundles = {}
        for case in cases:
            text = case.inputs(changes)
            bundles[case.id] = build_bundle(
                text["diff"], text["story"], repo=repo, as_of=case.as_of
            )
        done = _done(vdir)
        todo = [(c, r) for c in cases for r in range(reps) if (c.id, r) not in done]
        if not todo:
            print("nothing to run: every case and rep already has a row", file=sys.stderr)
        elif paid:
            estimate = estimate_cost(client.model, {c.id: bundles[c.id] for c, _ in todo}, 1)
            question = (
                f"About to run {len(todo)} plan(s) on {client.model} "
                f"(rough estimate {estimate or 'unknown'}; the real cost is printed after). "
                "Continue? [y/N] "
            )
            if not args.yes and not confirm(question):
                print("stopped before any call was made", file=sys.stderr)
                return 1
        _execute(todo, bundles, repo, client, vdir, args)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    for line in summarize(vdir):
        print(line)
    print(f"report: node build-report-lite.mjs {flow}  (see evals/README.md)", file=sys.stderr)
    return 0


def _execute(
    todo: List[Tuple[Case, int]],
    bundles: Dict[str, ContextBundle],
    repo: Path,
    client: LLMClient,
    vdir: Path,
    args: argparse.Namespace,
) -> None:
    vdir.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def one(item: Tuple[Case, int]) -> None:
        case, rep = item
        outcome = run_case(case, rep, bundles[case.id], repo, client, args.timeout_s)
        kind, row, err = record(case, rep, outcome, bundles[case.id], repo, client, vdir)
        with lock:
            target = vdir / ("results.jsonl" if kind == "row" else "errors.jsonl")
            with target.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row if kind == "row" else err, sort_keys=True) + "\n")
            label = row["status"] if row else f"failed ({err['class']})"
            print(f"{case.id} rep {rep}: {label}", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        list(pool.map(one, todo))


def _demo_repo(workdir: Path) -> Tuple[Path, Path]:
    sys.path.insert(0, str(ROOT / "demo"))
    import build_demo_repo

    repo, changes = workdir / "repo", workdir / "changes"
    build_demo_repo.build(repo, changes)
    return repo, changes


if __name__ == "__main__":
    sys.exit(main())
