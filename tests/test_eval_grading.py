"""Coverage grading and reviewer edit distance. No model is called anywhere here."""

from __future__ import annotations

import json

import pytest
from conftest import needs_git

from evals import compare, coverage, run
from evals.cases import CASES_DIR, load_cases
from testplan_agent.llm import HeuristicClient, Usage

pytestmark = needs_git
CASE = "s05-price-format"  # a demo case with two known defects


def run_variant(tmp_path, client=None, variant="baseline", only=(CASE,)):
    flow = tmp_path / "flow"
    factory = (lambda args: client) if client else run.make_client
    argv = ["--variant", variant, "--flow", str(flow), "--only", *only]
    assert run.main(argv, client_factory=factory) == 0
    return flow


def grades_file(tmp_path, grades, variant="baseline", grader="Tester"):
    path = tmp_path / "grades.json"
    path.write_text(json.dumps({"variant": variant, "grader": grader, "grades": grades}))
    return path


def rows(flow, variant="baseline"):
    return [json.loads(x) for x in (flow / variant / "results.jsonl").read_text().splitlines()]


CASES = load_cases(CASES_DIR)


# ---- the worksheet ----------------------------------------------------------------------------
def test_the_worksheet_lists_every_defect_of_every_plan(tmp_path):
    flow = run_variant(tmp_path)
    page = coverage.worksheet(flow, "baseline", CASES).read_text()
    assert page.count('class="defect"') == 2
    assert f'data-key="{CASE}/0/D1"' in page and f'data-key="{CASE}/0/D2"' in page
    assert page.count('type="radio"') == 6  # caught / partial / missed per defect
    assert "Download grades" in page


# ---- merging grades ---------------------------------------------------------------------------
def test_grades_become_coverage_metrics(tmp_path):
    flow = run_variant(tmp_path)
    path = grades_file(tmp_path, {
        f"{CASE}/0/D1": {"grade": "caught", "by": "TC-1"},
        f"{CASE}/0/D2": {"grade": "partial", "note": "negative amount not forced"},
    })  # fmt: skip
    counts = coverage.apply(flow, "baseline", CASES, path)
    assert counts == {"graded": 2, "defects": 2, "plans_scored": 1, "plans": 1}
    (row,) = rows(flow)
    assert row["grade"]["coverage"] == 0.5 and row["grade"]["coverage_lenient"] == 0.75
    stored = json.loads((flow / "baseline" / "coverage.json").read_text())
    assert stored["graders"] == ["Tester"] and stored["grades"][f"{CASE}/0/D1"]["by"] == "TC-1"


def test_a_plan_is_scored_only_when_all_its_defects_are_graded(tmp_path):
    flow = run_variant(tmp_path)
    coverage.apply(
        flow, "baseline", CASES, grades_file(tmp_path, {f"{CASE}/0/D1": {"grade": "caught"}})
    )
    (row,) = rows(flow)
    assert "coverage" not in row["grade"]  # half graded is not averaged in


def test_grading_can_happen_in_several_sittings(tmp_path):
    flow = run_variant(tmp_path)
    coverage.apply(
        flow, "baseline", CASES, grades_file(tmp_path, {f"{CASE}/0/D1": {"grade": "missed"}})
    )
    coverage.apply(
        flow, "baseline", CASES, grades_file(tmp_path, {f"{CASE}/0/D2": {"grade": "missed"}})
    )
    assert rows(flow)[0]["grade"]["coverage"] == 0.0


@pytest.mark.parametrize(
    "grades,variant,message",
    [
        ({"s05-price-format/0/D9": {"grade": "caught"}}, "baseline", "not a defect"),
        ({"s05-price-format/0/D1": {"grade": "maybe"}}, "baseline", "grade must be one of"),
        ({"s05-price-format/0/D1": {"grade": "caught"}}, "v7", "grades variant"),
    ],
)
def test_bad_grades_are_refused(tmp_path, grades, variant, message):
    flow = run_variant(tmp_path)
    with pytest.raises(coverage.CoverageError, match=message):
        coverage.apply(flow, "baseline", CASES, grades_file(tmp_path, grades, variant=variant))


def test_status_changes_nothing(tmp_path, capsys):
    flow = run_variant(tmp_path)
    before = (flow / "baseline" / "results.jsonl").read_text()
    assert coverage.main(["status", "--variant", "baseline", "--flow", str(flow)]) == 0
    assert "0 of 2 defect grades" in capsys.readouterr().out
    assert (flow / "baseline" / "results.jsonl").read_text() == before
    assert not (flow / "baseline" / "coverage.json").exists()


# ---- known-good and known-bad ------------------------------------------------------------------
def test_oracle_all_caught_scores_one(tmp_path):
    flow = run_variant(tmp_path)
    all_caught = {f"{CASE}/0/{d}": {"grade": "caught"} for d in ("D1", "D2")}
    coverage.apply(flow, "baseline", CASES, grades_file(tmp_path, all_caught))
    assert rows(flow)[0]["grade"]["coverage"] == 1.0


class EmptyPlan:
    """A planner whose plan has no test conditions at all."""

    name, model, deterministic = "empty", "empty", True

    def complete(self, system, turns):
        completion = HeuristicClient().complete(system, turns)
        plan = json.loads(completion.text)
        plan["cases"] = []
        completion.text, completion.usage = json.dumps(plan), Usage(model="empty")
        return completion


def test_null_a_plan_without_conditions_scores_zero_without_grading(tmp_path):
    flow = run_variant(tmp_path, client=EmptyPlan(), variant="v1")
    counts = coverage.apply(flow, "v1", CASES, None)
    assert counts["plans_scored"] == 1
    assert rows(flow, "v1")[0]["grade"]["coverage"] == 0.0
    page = coverage.worksheet(flow, "v1", CASES).read_text()
    assert 'data-auto="1"' in page and 'type="radio"' not in page


# ---- reviewer edit distance ---------------------------------------------------------------------
def _plan_md(tmp_path):
    flow = run_variant(tmp_path, only=("s02-free-shipping",))
    plan = json.loads((flow / "baseline" / "plans" / "s02-free-shipping_rep0.json").read_text())
    rows_md = "\n".join(f"| {c['id']} | {c['priority']} | {c['title']} |" for c in plan["cases"])
    return f"# Plan\n\n## 3. Test conditions\n\n{rows_md}\n\n## 4. Regression scope\n\nnone\n"


def test_an_untouched_plan_has_no_edit_distance(tmp_path):
    md = _plan_md(tmp_path)
    result = compare.compare(md, md)
    assert result["conditions"]["edit_distance"] == 0 and result["whole_plan"]["changed"] == 0
    assert result["condition_verdicts"]["reworded"] == []


def test_edits_are_counted_per_line_and_per_condition(tmp_path):
    md = _plan_md(tmp_path)
    lines = md.splitlines()
    first = next(i for i, x in enumerate(lines) if x.startswith("| TC-1 "))
    lines[first] = lines[first].replace("|", "| reviewed:", 2).replace("| reviewed:", "|", 1)
    second = next(i for i, x in enumerate(lines) if x.startswith("| TC-2 "))
    del lines[second]
    lines.insert(first + 1, "| TC-99 | P1 | exactly 50.00 euros ships free |")
    result = compare.compare(md, "\n".join(lines) + "\n")
    v = result["condition_verdicts"]
    assert v["reworded"] == ["TC-1"] and v["dropped"] == ["TC-2"] and v["added"] == ["TC-99"]
    assert 0 < result["conditions"]["edit_distance"] < 1
    assert result["conditions"]["changed"] + result["conditions"]["deleted"] >= 1
