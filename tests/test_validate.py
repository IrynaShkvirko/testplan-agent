"""One negative test for every validator code, so no check can silently stop working."""

from __future__ import annotations

import copy

import pytest

from testplan_agent.schema import TestPlan
from testplan_agent.validate import check_shape, has_errors, validate_plan


def run(fixture, mutate=None, bundle_mutate=None, **kwargs):
    plan, bundle = copy.deepcopy(fixture.plan), fixture.bundle
    if mutate:
        mutate(plan)
    if bundle_mutate:
        bundle_mutate(bundle)
    shape = check_shape(plan)
    if shape:
        return shape
    return validate_plan(TestPlan.from_dict(plan), bundle, fixture.repo, **kwargs)


def codes(issues, severity=None):
    return {i.code for i in issues if severity is None or i.severity == severity}


def case(plan, n=0):
    return plan["cases"][n]


def test_a_generated_plan_is_clean(discount):
    assert run(discount) == []


# ---- errors ---------------------------------------------------------------------------------
def test_schema_error_for_a_wrong_type(discount):
    issues = run(discount, lambda p: p.update(cases="not a list"))
    assert codes(issues) == {"schema"} and has_errors(issues)


def test_schema_error_for_a_value_outside_the_allowed_set(discount):
    issues = run(discount, lambda p: case(p).update(priority="P9"))
    assert "schema" in codes(issues)


def test_duplicate_id(discount):
    def mutate(p):
        case(p, 1)["id"] = case(p, 0)["id"]

    assert "duplicate_id" in codes(run(discount, mutate), "error")


def test_unknown_fact(discount):
    assert "unknown_fact" in codes(
        run(discount, lambda p: case(p)["evidence"].append("F999")), "error"
    )


@pytest.mark.parametrize(
    "ref", ["shop/pricing.py:9999", "no/such/file.py:1", "shop/../../etc/passwd:1", "nonsense"]
)
def test_bad_file_ref(discount, ref):
    assert "bad_file_ref" in codes(
        run(discount, lambda p: case(p)["evidence"].append(ref)), "error"
    )


def test_a_real_line_in_a_changed_file_is_accepted(discount):
    assert run(discount, lambda p: case(p)["evidence"].append("shop/pricing.py:8")) == []


def test_unknown_risk_in_the_risk_list(discount):
    def mutate(p):
        p["risks"].append({**copy.deepcopy(p["risks"][0]), "id": "R99"})

    assert "unknown_risk" in codes(run(discount, mutate), "error")


def test_unknown_risk_on_a_case(discount):
    assert "unknown_risk" in codes(run(discount, lambda p: case(p).update(risk="R99")), "error")


def test_risk_mismatch(discount):
    assert "risk_mismatch" in codes(
        run(discount, lambda p: p["risks"][0].update(likelihood=1)), "error"
    )


def test_a_written_adjustment_of_one_step_is_allowed(discount):
    def mutate(p):
        r = p["risks"][3]
        r["likelihood"] -= 1
        r["adjustment"] = {
            "likelihood_delta": -1,
            "impact_delta": 0,
            "reason": "only one caller, in tests",
        }

    assert run(discount, mutate) == []


def test_adjustment_without_reason(discount):
    def mutate(p):
        r = p["risks"][3]
        r["likelihood"] -= 1
        r["adjustment"] = {"likelihood_delta": -1, "impact_delta": 0, "reason": "  "}

    assert "adjustment_without_reason" in codes(run(discount, mutate), "error")


def test_risk_dropped(discount):
    def mutate(p):
        p["risks"] = [r for r in p["risks"] if r["id"] != "R1"]
        for c in p["cases"]:
            if c["risk"] == "R1":
                c["risk"] = "R2"

    assert "risk_dropped" in codes(run(discount, mutate), "error")


def test_missing_requirement_reason(discount):
    def mutate(p):
        case(p).update(requirement="none", requirement_reason="")

    assert "missing_requirement_reason" in codes(run(discount, mutate), "error")


def test_unknown_requirement(discount):
    assert "unknown_requirement" in codes(
        run(discount, lambda p: case(p).update(requirement="AC-99")), "error"
    )


def test_unknown_test(discount):
    assert "unknown_test" in codes(
        run(discount, lambda p: case(p).update(existing_coverage="tests/x.py::test_invented")),
        "error",
    )


def test_unknown_test_in_regression_scope(discount):
    def mutate(p):
        p["regression_scope"].append(
            {"test": "tests/x.py::nope", "reason": "r", "evidence": ["F1"]}
        )

    assert "unknown_test" in codes(run(discount, mutate), "error")


def test_missing_steps_on_a_p0_case(discount):
    def mutate(p):
        target = next(c for c in p["cases"] if c["priority"] == "P0")
        target["steps"] = []

    assert "missing_steps" in codes(run(discount, mutate), "error")


def test_uncovered_requirement(discount):
    def mutate(p):
        p["cases"] = [c for c in p["cases"] if c["requirement"] != "AC-3"]

    assert "uncovered_requirement" in codes(run(discount, mutate), "error")


# ---- warnings -------------------------------------------------------------------------------
def test_priority_mismatch_is_a_warning(discount):
    def bundle_mutate(bundle):
        bundle.risk_by_id()["R1"].impact = 1
        bundle.risk_by_id()["R1"].likelihood = 1

    issues = run(discount, bundle_mutate=bundle_mutate)
    assert "priority_mismatch" in codes(issues, "warning")


def test_quarantined_coverage_is_a_warning(discount):
    tid = next(t.nodeid for t in discount.bundle.existing_tests)

    def mutate(p):
        case(p).update(existing_coverage=tid)

    def bundle_mutate(bundle):
        for t in bundle.existing_tests:
            if t.nodeid == tid:
                t.quarantined = True

    issues = run(discount, mutate, bundle_mutate)
    assert "quarantined_coverage" in codes(issues, "warning")
    assert not has_errors(issues)


def test_missing_confidence_reason_is_a_warning(discount):
    issues = run(discount, lambda p: case(p).update(confidence="low", confidence_reason=""))
    assert "missing_confidence_reason" in codes(issues, "warning")


def test_a_high_risk_with_no_condition_is_a_warning(discount):
    def mutate(p):
        for c in p["cases"]:
            if c["risk"] == "R1":
                c["risk"] = "R2"

    issues = run(discount, mutate)
    assert "risk_without_cases" in codes(issues, "warning") and not has_errors(issues)


def test_too_many_cases_is_a_warning(discount):
    issues = run(discount, max_cases_per_risk=1)
    assert "too_many_cases" in codes(issues, "warning") and not has_errors(issues)


def test_a_flag_that_was_not_raised_is_a_warning(discount):
    issues = run(discount, lambda p: p.update(open_questions=[]))
    assert "unraised_flag" in codes(issues, "warning")


def test_class_mismatch_is_a_warning(discount):
    issues = run(discount, lambda p: p["summary"].update(change_class="bugfix"))
    assert "class_mismatch" in codes(issues, "warning")


def test_every_code_in_the_validator_has_a_test():
    """Guards the guard: adding a check without a negative test fails here."""
    import inspect
    import re

    import testplan_agent.validate as v

    source = inspect.getsource(v.validate_plan) + inspect.getsource(v.check_shape)
    used = set(re.findall(r'(?:err|warn)\(\s*"(\w+)"', source)) | {"schema"}
    tested = set(re.findall(r'"(\w+)" in codes', open(__file__).read()))
    tested |= {"schema"}
    assert used <= tested, f"no negative test for: {sorted(used - tested)}"
