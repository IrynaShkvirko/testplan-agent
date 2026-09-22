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
    # the free grades are there at once; coverage waits for a person (evals/coverage.py)
    assert set(row["grade"]) == {m["id"] for m in grading.METRICS} - {
        "coverage",
        "coverage_lenient",
    }
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


# ---- the case set ---------------------------------------------------------------------------
def test_every_committed_case_loads_with_complete_labels():
    from evals.cases import CASES_DIR

    cases = load_cases(CASES_DIR)
    assert len(cases) >= 14
    for case in cases:
        assert case.defects, case.id
        for d in case.defects:
            assert d.summary and d.trigger and d.caught_if and d.written_by, (case.id, d.id)
            path, line = d.location.rsplit(":", 1)
            assert path and int(line) > 0, (case.id, d.id)


def test_the_committed_synthetic_cases_are_current(tmp_path):
    """If this fails, run ``python -m evals.synthetic`` and commit the result."""
    from evals import synthetic

    ids = synthetic.generate(tmp_path, keep_from=synthetic.CASES_DIR)
    for case_id in ids:
        for name in ("case.json", "change.patch", "story.md"):
            committed = synthetic.CASES_DIR / case_id / name
            assert committed.read_text() == (tmp_path / case_id / name).read_text(), (case_id, name)


def test_a_synthetic_label_points_at_a_changed_line():
    from evals import synthetic
    from testplan_agent.diffparse import parse_diff

    for spec in synthetic.SPECS:
        folder = synthetic.CASES_DIR / spec["id"]
        changes = {c.path: c for c in parse_diff((folder / "change.patch").read_text())}
        for d in json.loads((folder / "case.json").read_text())["defects"]:
            path, line = d["location"].rsplit(":", 1)
            assert int(line) in changes[path].added_line_numbers(), (spec["id"], d["id"])


def test_regenerating_keeps_a_verification_of_an_unchanged_label(tmp_path):
    from evals import synthetic

    synthetic.generate(tmp_path)
    path = tmp_path / "s05-price-format" / "case.json"
    case = json.loads(path.read_text())
    case["defects"][0]["verified_by"] = "Reviewer"
    path.write_text(json.dumps(case))
    synthetic.generate(tmp_path)
    assert json.loads(path.read_text())["defects"][0]["verified_by"] == "Reviewer"


def _git(repo, *args):
    import subprocess

    env = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "T",
           "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": __import__("os").environ["PATH"],
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_DATE": "2026-05-01T10:00:00",
           "GIT_COMMITTER_DATE": "2026-05-01T10:00:00"}  # fmt: skip
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def test_a_git_case_plans_the_commit_against_its_parent(tmp_path, monkeypatch):
    upstream = tmp_path / "upstream"
    (upstream / "lib").mkdir(parents=True)
    (upstream / "lib" / "calc.py").write_text("def half(x):\n    return x / 2\n")
    _git(upstream, "init", "-q", "-b", "main")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-qm", "feat: half")
    (upstream / "lib" / "calc.py").write_text("def half(x):\n    return x // 2\n")
    _git(upstream, "commit", "-qam", "Use integer halves\n\nKeep results whole numbers.")
    import subprocess

    sha = subprocess.run(["git", "-C", str(upstream), "rev-parse", "HEAD"], capture_output=True,
                         text=True).stdout.strip()  # fmt: skip
    cases = tmp_path / "cases" / "g01-half"
    cases.mkdir(parents=True)
    (cases / "case.json").write_text(json.dumps({
        "id": "g01-half", "title": "Integer halves", "tags": ["real"], "as_of": "2026-05-01",
        "repo": {"kind": "git", "url": upstream.as_uri(), "commit": sha},
        "defects": [{"id": "D1", "summary": "odd numbers lose .5", "location": "lib/calc.py:2",
                     "trigger": "half(3)", "caught_if": "checks half(3)", "written_by": "t"}],
    }))  # fmt: skip
    monkeypatch.setattr("evals.cases.CACHE_DIR", tmp_path / "cache")
    flow = tmp_path / "flow"
    code = run.main(
        ["--variant", "baseline", "--cases", str(tmp_path / "cases"), "--flow", str(flow)]
    )
    assert code == 0
    (row,) = rows(flow)
    assert row["status"] == "ok"
    trace = json.loads((flow / "baseline" / "traces" / "g01-half_rep0.json").read_text())
    assert "Use integer halves" in trace[1]["content"] and "x // 2" in trace[1]["content"]


def test_the_review_page_shows_every_label_with_its_line(tmp_path):
    from evals import review
    from evals.cases import CASES_DIR

    cases = [c for c in load_cases(CASES_DIR) if c.repo["kind"] == "demo"][:3]
    page = review.build(cases, tmp_path / "review.html").read_text()
    labels = sum(len(c.defects) for c in cases)
    assert page.count('class="defect"') == labels
    # every label shows its status; the summary line also says "not verified yet"
    assert page.count("verified by ") + page.count("not verified yet") - 1 == labels
    assert "if subtotal &gt; FREE_SHIPPING_FROM_CENTS:" in page or "subtotal" in page
