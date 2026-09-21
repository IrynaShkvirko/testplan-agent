"""Change classification and the risk model."""

from __future__ import annotations

import pytest

from testplan_agent import risk
from testplan_agent.classify import classify
from testplan_agent.diffparse import parse_diff
from testplan_agent.facts import FactStore
from testplan_agent.surface import summarize


def _classify(diff, title="", description="", criteria=False, tmp=None):
    summaries = [summarize(c, tmp) for c in parse_diff(diff)]
    return classify(summaries, title, description, criteria)


def _new(path, body="x = 1\n"):
    lines = body.splitlines()
    return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(
        f"+{x}\n" for x in lines
    )


@pytest.mark.parametrize(
    "diff,title,expected",
    [
        (_new("docs/a.md", "text"), "", "docs"),
        (_new("tests/test_a.py", "def test_a():\n    pass"), "", "test-only"),
        (_new("migrations/0001_x.sql", "ALTER TABLE a ADD b int;"), "", "migration"),
        (_new("requirements.txt", "flask==3.0"), "", "dependency"),
        (_new(".github/workflows/ci.yml", "on: push"), "", "config"),
        (_new("app/new_module.py", "def f():\n    return 1"), "Add reports", "feature"),
    ],
)
def test_classes(diff, title, expected):
    cls, _, reasons = _classify(diff, title)
    assert cls == expected and reasons


def test_a_bug_fix_title_with_no_new_definitions_is_a_bugfix():
    diff = "--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
    cls, _, _ = _classify(diff, title="Fix wrong total")
    assert cls == "bugfix"


def test_traits_record_what_kind_of_change_it_is():
    diff = _new("app/api.py", "@app.post('/x')\ndef make():\n    return 1")
    _, traits, _ = _classify(diff, title="Add endpoint")
    assert "new_endpoint" in traits and "new_module" in traits


# ---- risk ---------------------------------------------------------------------------------
def _facts(**kinds):
    store = FactStore()
    for kind, items in kinds.items():
        for text, data in items:
            store.add(kind, text, "test", **data)
    return store


def test_levels_use_the_documented_thresholds():
    assert risk.level_for(risk.HIGH_AT) == "high"
    assert risk.level_for(risk.HIGH_AT - 1) == "medium"
    assert risk.level_for(risk.MEDIUM_AT) == "medium"
    assert risk.level_for(risk.MEDIUM_AT - 1) == "low"


def test_a_sensitive_area_sets_impact_and_evidence():
    facts = _facts(
        change_file=[
            ("a.py modified", {"path": "a.py", "file_kind": "source", "added": 5, "removed": 1})
        ],
        sensitive_area=[("Money touched", {"area": "money", "paths": ["a.py"]})],
    )
    items = risk.assess(facts)
    money = next(r for r in items if r.kind == "area:money")
    assert money.impact == 5
    assert money.evidence == [facts.by_kind("sensitive_area")[0].id]
    assert money.id == "R1"


def test_likelihood_grows_with_each_factor_and_is_capped_at_five():
    facts = _facts(
        change_file=[("a", {"path": "a.py", "file_kind": "source", "added": 400, "removed": 0})],
        symbol=[
            ("new", {"status": "added", "sym_kind": "function", "path": "a.py", "qualname": "f"}),
            (
                "sig",
                {
                    "signature_change": "(a) -> (a, b)",
                    "sym_kind": "function",
                    "path": "a.py",
                    "qualname": "g",
                },
            ),
        ],
        git_history=[("hist", {"path": "a.py", "commits_90d": 9, "bugfixes_365d": 4})],
        untested_symbol=[(f"u{i}", {"qualname": f"u{i}", "path": "a.py"}) for i in range(3)],
        sensitive_area=[("Auth", {"area": "auth", "paths": ["a.py"]})],
    )
    likelihood, factors = risk.overall_likelihood(facts)
    assert likelihood == 5
    assert {f.name for f in factors} >= {
        "size",
        "new_code",
        "churn",
        "bug_history",
        "contract_change",
        "untested_change",
    }


def test_every_factor_names_the_facts_it_came_from():
    facts = _facts(
        symbol=[
            (
                "sig",
                {"signature_change": "x", "sym_kind": "function", "path": "a.py", "qualname": "g"},
            )
        ],
        sensitive_area=[("Time", {"area": "time", "paths": ["a.py"]})],
    )
    for item in risk.assess(facts):
        for factor in item.likelihood_factors + item.impact_factors:
            assert all(facts.get(fid) is not None for fid in factor.fact_ids)


def test_risks_are_ranked_by_score_and_numbered_from_one():
    facts = _facts(
        sensitive_area=[
            ("Time", {"area": "time", "paths": ["a.py"]}),
            ("Auth", {"area": "auth", "paths": ["b.py"]}),
        ]
    )
    items = risk.assess(facts)
    assert [r.id for r in items] == [f"R{n}" for n in range(1, len(items) + 1)]
    assert [r.score for r in items] == sorted((r.score for r in items), reverse=True)
    assert items[0].kind == "area:auth"


def test_risk_round_trips_through_a_dict():
    facts = _facts(sensitive_area=[("Money", {"area": "money", "paths": ["a.py"]})])
    (item,) = risk.assess(facts)
    again = risk.from_dict(risk.to_dict(item))
    assert (again.id, again.score, again.evidence) == (item.id, item.score, item.evidence)
    assert [f.name for f in again.impact_factors] == [f.name for f in item.impact_factors]


def test_docs_only_changes_produce_no_general_risk():
    facts = _facts(change_class=[("docs", {"class": "docs", "traits": []})])
    assert risk.assess(facts) == []
