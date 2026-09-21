import re

from testplan_agent.render import cited_facts, render_markdown
from testplan_agent.schema import TestPlan

SECTIONS = [
    "## 1. Summary and scope",
    "## 2. Risk assessment",
    "## 3. Test conditions",
    "## 4. Regression scope",
    "## 5. Coverage gaps",
    "## 6. Open questions and assumptions",
    "## 7. Environment and data needs",
    "## 8. Exit criteria",
    "## 9. Appendix",
]


def _render(discount):
    plan = TestPlan.from_dict(discount.plan)
    return plan, render_markdown(plan, discount.bundle)


def test_all_sections_appear_in_order(discount):
    _, text = _render(discount)
    positions = [text.index(h) for h in SECTIONS]
    assert positions == sorted(positions)


def test_the_plan_is_labelled_as_an_unreviewed_draft(discount):
    _, text = _render(discount)
    assert text.splitlines()[0].startswith("# Test plan:")
    assert "**Draft.**" in text and "Nothing here has been run" in text
    assert "heuristic-baseline" in text


def test_every_case_and_risk_id_is_shown(discount):
    plan, text = _render(discount)
    for c in plan.cases:
        assert f"| {c.id} |" in text
    for r in plan.risks:
        assert f"| {r.id} |" in text


def test_the_scores_are_explained_from_facts(discount):
    _, text = _render(discount)
    assert "How the scores were reached" in text
    assert re.search(r"R1\. Likelihood: .*\+\d", text)


def test_every_fact_cited_anywhere_is_listed_in_the_appendix(discount):
    plan, text = _render(discount)
    appendix = text[text.index("### Facts cited") :]
    for fid in cited_facts(plan, discount.bundle):
        assert f"| {fid} |" in appendix


def test_table_cells_survive_pipes_and_newlines(discount):
    discount.plan["cases"][0]["title"] = "a | b\nc"
    _, text = _render(discount)
    assert "a \\| b c" in text


def test_plan_errors_are_shown_not_hidden(discount):
    discount.plan["validation"] = [
        {
            "code": "unknown_fact",
            "severity": "error",
            "where": "case TC-1",
            "message": "F999 is not a fact",
        }
    ]
    _, text = _render(discount)
    assert "unknown_fact" in text and "F999" in text
    assert "All checks passed" not in text


def test_rendering_is_deterministic(discount):
    assert _render(discount)[1] == _render(discount)[1]


def _attempt(n, cost, latency=2500):
    return {
        "attempt": n,
        "model": "claude-opus-5",
        "input_tokens": 9000,
        "output_tokens": 4000,
        "cache_read_tokens": 8500 if n > 1 else 0,
        "cache_write_tokens": 0 if n > 1 else 8500,
        "latency_ms": latency,
        "cost_usd": cost,
        "stop_reason": "end_turn",
        "request_id": f"req_{n}",
    }


def test_model_calls_are_shown_per_attempt_with_totals(discount):
    plan = TestPlan.from_dict(discount.plan)
    plan.meta["run"] = run_details_for([_attempt(1, 0.1512), _attempt(2, 0.1045)])
    text = render_markdown(plan, discount.bundle)
    assert "- Model calls: 2; 18,000 tokens in, 8,000 out (8,500 read from cache); 5.0 s" in text
    assert "about $0.26" in text
    assert "| 2 | claude-opus-5 | 9,000 | 4,000 | 8,500 / 0 | 2.5 s | $0.1045 | end_turn |" in text
    assert "estimates from list prices" in text


def test_an_unknown_cost_is_not_shown_as_zero(discount):
    plan = TestPlan.from_dict(discount.plan)
    plan.meta["run"] = run_details_for([_attempt(1, None, latency=None)])
    text = render_markdown(plan, discount.bundle)
    assert "cost unknown" in text and "no model time" in text and "$0.00" not in text


def test_a_run_without_a_model_says_so(discount):
    _, text = _render(discount)
    assert "- Model calls: none (1 attempt(s), no tokens used)" in text


def run_details_for(attempts):
    from testplan_agent.llm import Usage
    from testplan_agent.planner import run_details

    fields = [{k: v for k, v in a.items() if k != "attempt"} for a in attempts]
    return run_details([Usage(**f) for f in fields])
