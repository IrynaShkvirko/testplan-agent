"""The evaluation runner: rows, resume, failure classes, time limit, and the paid-run gate.

Every client here is local (the rule-based baseline or a wrapper around it): nothing is paid.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from conftest import needs_git

from evals import grading, run
from evals.cases import CaseError, load_cases
from testplan_agent.llm import HeuristicClient, LLMError, Usage

CASES = Path(__file__).parent / "fixtures" / "eval_cases"
ONE = ["--only", "smoke-discount-cap"]

pytestmark = needs_git  # the demo repository is a git checkout


def rows(flow, variant="baseline", name="results.jsonl"):
    path = flow / variant / name
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def evals(tmp_path, *argv, client=None, confirm=lambda _: True):
    factory = (lambda args: client) if client is not None else run.make_client
    base = ["--cases", str(CASES), "--flow", str(tmp_path / "flow")]
    return run.main([*base, *argv], client_factory=factory, confirm=confirm)


class Wrapped:
    """The baseline's answers under another name, with a hook to change what comes back."""

    def __init__(self, name="scripted", model="scripted", deterministic=False, hook=None):
        self.name, self.model, self.deterministic = name, model, deterministic
        self.inner = HeuristicClient()
        self.hook = hook
        self.calls = 0

    def complete(self, system, turns):
        self.calls += 1
        completion = self.inner.complete(system, turns)
        completion.usage = Usage(model=self.model, input_tokens=100, output_tokens=50)
        return self.hook(completion) if self.hook else completion


# ---- a clean run ------------------------------------------------------------------------------
def test_a_run_writes_a_complete_row_plan_and_trace_per_case(tmp_path, capsys):
    assert evals(tmp_path, "--variant", "baseline") == 0
    flow = tmp_path / "flow"
    results = rows(flow)
    assert sorted(r["prompt_id"] for r in results) == [
        "smoke-discount-cap",
        "smoke-refund-endpoint",
        "smoke-reservation-timeout",
    ]
    row = results[0]
    for field in ("prompt", "tags", "status", "model", "grade", "usage", "attempts", "wall_s"):
        assert field in row
    assert row["status"] == "ok" and row["model"] == "none (rule-based)"
    assert set(row["grade"]) == {m["id"] for m in grading.METRICS}
    assert (flow / "baseline" / row["meta"]["plan"]).is_file()
    trace = json.loads((flow / "baseline" / "traces" / f"{row['prompt_id']}_rep0.json").read_text())
    assert [t["role"] for t in trace] == ["system", "user", "assistant"]
    assert json.loads((flow / "_state.json").read_text())["metrics"] == grading.METRICS
    assert "Checks clean" in capsys.readouterr().out


def test_a_rerun_resumes_instead_of_repeating(tmp_path):
    evals(tmp_path, "--variant", "baseline", *ONE)
    evals(tmp_path, "--variant", "baseline")
    ids = [r["prompt_id"] for r in rows(tmp_path / "flow")]
    assert len(ids) == len(set(ids)) == 3


def test_a_deterministic_client_runs_one_rep(tmp_path, capsys):
    evals(tmp_path, "--variant", "baseline", "--reps", "3", *ONE)
    assert [r["rep"] for r in rows(tmp_path / "flow")] == [0]
    assert "running 1 rep" in capsys.readouterr().err


def test_reps_are_kept_apart(tmp_path):
    evals(tmp_path, "--variant", "v1", "--reps", "2", *ONE, client=Wrapped())
    assert sorted(r["rep"] for r in rows(tmp_path / "flow", "v1")) == [0, 1]


# ---- outcomes that are not a plan -------------------------------------------------------------
@pytest.mark.parametrize("kind,status", [("refusal", "refused"), ("truncated", "truncated")])
def test_a_refusal_or_cut_off_answer_is_counted_but_not_scored(tmp_path, kind, status):
    def fail(completion):
        raise LLMError("no", usage=Usage(model="m", input_tokens=10, cost_usd=0.01), kind=kind)

    evals(tmp_path, "--variant", "v1", *ONE, client=Wrapped(hook=fail))
    (row,) = rows(tmp_path / "flow", "v1")
    assert row["status"] == status and row["grade"] == {}
    assert row["cost_usd"] == 0.01  # it was paid for
    assert rows(tmp_path / "flow", "v1", "errors.jsonl") == []


def test_an_api_failure_goes_to_the_errors_file_and_is_retried_next_run(tmp_path):
    def fail(completion):
        raise LLMError("slow down", kind="rate_limit")

    evals(tmp_path, "--variant", "v1", *ONE, client=Wrapped(hook=fail))
    assert rows(tmp_path / "flow", "v1") == []
    (err,) = rows(tmp_path / "flow", "v1", "errors.jsonl")
    assert err["class"] == "rate_limit" and err["prompt_id"] == "smoke-discount-cap"
    evals(tmp_path, "--variant", "v1", *ONE, client=Wrapped())
    assert [r["status"] for r in rows(tmp_path / "flow", "v1")] == ["ok"]


def test_a_crash_is_a_harness_error_not_a_bad_plan(tmp_path):
    def crash(completion):
        raise RuntimeError("bug in the harness")

    evals(tmp_path, "--variant", "v1", *ONE, client=Wrapped(hook=crash))
    (err,) = rows(tmp_path / "flow", "v1", "errors.jsonl")
    assert err["class"] == "harness_error" and "bug in the harness" in err["message"]


def test_a_case_that_runs_too_long_is_a_timeout(tmp_path):
    def slow(completion):
        time.sleep(2)
        return completion

    evals(tmp_path, "--variant", "v1", "--timeout-s", "0.3", *ONE, client=Wrapped(hook=slow))
    (err,) = rows(tmp_path / "flow", "v1", "errors.jsonl")
    assert err["class"] == "timeout"


# ---- paid runs ------------------------------------------------------------------------------
def paid(**kwargs):
    return Wrapped(name="anthropic", model="claude-opus-5", **kwargs)


def test_a_paid_run_needs_the_harness_approved_first(tmp_path, capsys):
    client = paid()
    assert evals(tmp_path, "--variant", "v1", *ONE, client=client) == 2
    assert "--approve-harness" in capsys.readouterr().err and client.calls == 0


def test_a_paid_run_asks_before_spending(tmp_path, capsys):
    client = paid()
    asked = []
    code = evals(
        tmp_path, "--variant", "v1", "--approve-harness", *ONE,
        client=client, confirm=lambda text: asked.append(text) or False,
    )  # fmt: skip
    assert code == 1 and client.calls == 0
    assert "claude-opus-5" in asked[0] and "estimate $" in asked[0]
    assert evals(tmp_path, "--variant", "v1", "--yes", *ONE, client=client) == 0
    assert client.calls == 1


def test_a_changed_harness_needs_approving_again(tmp_path):
    flow = tmp_path / "flow"
    evals(tmp_path, "--variant", "v1", "--approve-harness", "--yes", *ONE, client=paid())
    state = json.loads((flow / "_state.json").read_text())
    state["harness_sha"] = "0" * 64  # as if the code had changed since
    (flow / "_state.json").write_text(json.dumps(state))
    assert evals(tmp_path, "--variant", "v2", "--yes", *ONE, client=paid()) == 2


def test_an_answer_from_another_model_is_not_scored(tmp_path):
    def reroute(completion):
        completion.usage.model = "claude-other-9"
        return completion

    evals(tmp_path, "--variant", "v1", "--approve-harness", "--yes", *ONE,
          client=paid(hook=reroute))  # fmt: skip
    assert rows(tmp_path / "flow", "v1") == []
    (err,) = rows(tmp_path / "flow", "v1", "errors.jsonl")
    assert err["class"] == "served_model_mismatch" and err["model"] == "claude-other-9"


# ---- grades ---------------------------------------------------------------------------------
def test_first_attempt_citation_accuracy(discount):
    good = json.dumps(discount.plan)
    assert grading.citation_accuracy(good, discount.bundle, discount.repo) == 1.0
    bad = json.loads(good)
    bad["cases"][0]["evidence"] = ["F998", "F999"]
    accuracy = grading.citation_accuracy(json.dumps(bad), discount.bundle, discount.repo)
    assert 0 < accuracy < 1
    # no plan at all is "nothing to count", not "every citation wrong"
    assert grading.citation_accuracy("sorry", discount.bundle, discount.repo) is None


def test_a_plan_with_check_errors_is_not_clean(discount):
    from testplan_agent.schema import TestPlan

    plan = TestPlan.from_dict(discount.plan)
    plan.validation = [{"code": "unknown_fact", "severity": "error", "where": "", "message": ""}]
    grades = grading.grade(plan, 3, json.dumps(discount.plan), discount.bundle, discount.repo)
    assert grades["checks_clean"] == 0.0 and grades["first_try_clean"] == 0.0


# ---- arguments and cases --------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [["--variant", "better-prompt"], ["--variant", "baseline", "--model", "claude-sonnet-5"]],
)
def test_bad_arguments_are_refused(tmp_path, argv):
    assert evals(tmp_path, *argv) == 2


def test_cases_are_validated(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "case.json").write_text(
        json.dumps({"id": "y", "title": "t", "as_of": "2026-01-01"})
    )
    with pytest.raises(CaseError, match="does not match its folder"):
        load_cases(tmp_path)
    with pytest.raises(CaseError, match="unknown case"):
        load_cases(CASES, ["nope"])
