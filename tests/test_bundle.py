"""The context bundle: what gets collected, what is redacted, what a model would see."""

from __future__ import annotations

import json
from datetime import date

import pytest
from conftest import AS_OF, dumps, read_change

from testplan_agent.bundle import ContextBundle, build_bundle
from testplan_agent.diffparse import DiffError

DAY = date.fromisoformat(AS_OF)


def test_bundle_round_trips_through_json(demo_bundles):
    for bundle in demo_bundles.values():
        again = ContextBundle.from_dict(json.loads(dumps(bundle.to_dict())))
        assert dumps(again.to_dict()) == dumps(bundle.to_dict())


def test_the_same_inputs_give_the_same_bundle(demo):
    diff, story = read_change(demo, "discount-cap")
    first = build_bundle(diff, story, repo=demo.repo, as_of=DAY)
    second = build_bundle(diff, story, repo=demo.repo, as_of=DAY)
    assert dumps(first.to_dict()) == dumps(second.to_dict())


def test_fact_ids_are_unique_and_every_risk_evidence_id_exists(demo_bundles):
    for bundle in demo_bundles.values():
        ids = [f.id for f in bundle.facts.all()]
        assert len(ids) == len(set(ids))
        for r in bundle.risks:
            assert all(bundle.facts.get(x) for x in r.evidence)


def test_discount_cap_findings(demo_bundles):
    b = demo_bundles["discount-cap"]
    assert b.change_class["class"] == "feature"
    assert [r.id for r in b.criteria()] == ["AC-1", "AC-2", "AC-3", "AC-4"]
    assert {a.term for a in b.ambiguities} == {"large", "reasonable"}
    sig = [f for f in b.facts.by_kind("symbol") if f.data.get("signature_change")]
    assert [f.data["qualname"] for f in sig] == ["apply_discount"]
    assert any("shop/api.py" == d.path for d in b.dependents)
    assert any(t.strength == "direct" for t in b.existing_tests)


def test_reservation_timeout_findings(demo_bundles):
    b = demo_bundles["reservation-timeout"]
    areas = {f.data["area"] for f in b.facts.by_kind("sensitive_area")}
    assert {"concurrency", "time", "data_loss"} <= areas
    assert b.risks[0].level == "high"
    assert any(f.data["bugfixes_365d"] >= 1 for f in b.facts.by_kind("git_history"))


def test_a_class_is_not_reported_separately_from_its_changed_methods(demo_bundles):
    b = demo_bundles["reservation-timeout"]
    names = {f.data["qualname"] for f in b.facts.by_kind("untested_symbol")}
    names |= {f.data["qualname"] for f in b.facts.by_kind("unmapped_change")}
    assert "Inventory" not in names


def test_secrets_never_reach_the_bundle(demo_bundles):
    b = demo_bundles["refund-endpoint"]
    blob = dumps(b.to_dict())
    assert "hunter2" not in blob
    assert b.meta["redactions"].get("credential_assignment", 0) >= 1
    assert b.facts.by_kind("redaction")


def test_redaction_does_not_break_hunk_line_numbers(demo_bundles):
    b = demo_bundles["refund-endpoint"]
    refunds = b.change_by_path()["shop/refunds.py"]
    assert refunds.new_ranges and refunds.line_count == refunds.new_ranges[-1][1]


def test_without_a_repo_the_bundle_says_what_was_skipped(demo):
    diff, story = read_change(demo, "discount-cap")
    b = build_bundle(diff, story, repo=None, as_of=DAY)
    assert any("no repository" in w for w in b.warnings)
    assert b.existing_tests == [] and b.dependents == []


def test_a_missing_repo_directory_is_an_error(demo, tmp_path):
    diff, story = read_change(demo, "discount-cap")
    with pytest.raises(FileNotFoundError):
        build_bundle(diff, story, repo=tmp_path / "nope", as_of=DAY)


def test_an_unreadable_diff_is_an_error():
    with pytest.raises(DiffError):
        build_bundle("this is not a diff", "", repo=None, as_of=DAY)


def test_a_small_context_budget_names_what_was_left_out(demo):
    diff, story = read_change(demo, "reservation-timeout")
    b = build_bundle(diff, story, repo=demo.repo, as_of=DAY, max_context_chars=400)
    notes = [w for w in b.warnings if "budget" in w]
    assert notes
    assert sum(len(x["patch"]) for x in b.diff_excerpt) < 1200
    # the omissions are also facts, so a plan can cite them
    assert len(b.facts.by_kind("warning")) >= len(notes)


def test_quarantined_tests_are_marked(demo, tmp_path):
    diff, story = read_change(demo, "discount-cap")
    plain = build_bundle(diff, story, repo=demo.repo, as_of=DAY)
    target = next(t.nodeid for t in plain.existing_tests if t.strength == "direct")
    quarantine = tmp_path / "quarantine.json"
    quarantine.write_text(json.dumps({"tests": {target: {}}}))
    b = build_bundle(diff, story, repo=demo.repo, as_of=DAY, quarantine_path=quarantine)
    assert {t.nodeid for t in b.existing_tests if t.quarantined} == {target}


def test_a_bad_coverage_file_becomes_a_warning_not_a_crash(demo, tmp_path):
    diff, story = read_change(demo, "discount-cap")
    bad = tmp_path / "coverage.xml"
    bad.write_text("<!DOCTYPE x><coverage/>")
    b = build_bundle(diff, story, repo=demo.repo, as_of=DAY, coverage_path=bad)
    assert any("coverage data ignored" in w for w in b.warnings)


def test_coverage_gaps_become_facts(demo, tmp_path):
    diff, story = read_change(demo, "discount-cap")
    xml = tmp_path / "coverage.xml"
    xml.write_text(
        '<coverage><packages><package><classes><class filename="shop/pricing.py"><lines>'
        + "".join(f'<line number="{n}" hits="0"/>' for n in range(1, 40))
        + "</lines></class></classes></package></packages></coverage>"
    )
    b = build_bundle(diff, story, repo=demo.repo, as_of=DAY, coverage_path=xml)
    (gap,) = b.facts.by_kind("uncovered_lines")
    assert gap.data["path"] == "shop/pricing.py" and gap.data["lines"]


def test_a_bundle_of_the_wrong_version_is_refused(demo_bundles):
    raw = demo_bundles["discount-cap"].to_dict()
    raw["version"] = 999
    with pytest.raises(ValueError):
        ContextBundle.from_dict(raw)


def test_constraints_are_carried_into_the_bundle(demo):
    diff, story = read_change(demo, "discount-cap")
    b = build_bundle(diff, story, repo=None, as_of=DAY, constraints={"levels": ["unit"]})
    assert b.meta["constraints"] == {"levels": ["unit"]}
