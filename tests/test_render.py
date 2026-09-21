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
