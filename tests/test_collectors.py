"""Existing-test scan, import graph, git history, coverage, environment detection."""

from __future__ import annotations

import json
from datetime import date

import pytest
from conftest import git, needs_git, write_files

from testplan_agent import coverage, environment, gitsignals, importgraph, testscan
from testplan_agent.diffparse import parse_diff
from testplan_agent.surface import summarize

SHOP = {
    "shop/__init__.py": "",
    "shop/pricing.py": "def apply_discount(price, pct):\n    return price - price * pct // 100\n",
    "shop/api.py": "from shop.pricing import apply_discount\n\n\ndef quote(p):\n    return apply_discount(p, 10)\n",
    "shop/web.py": "from shop import api\n",
    "tests/test_pricing.py": (
        "from shop.pricing import apply_discount\n\n\n"
        "def test_ten_percent():\n    assert apply_discount(100, 10) == 90\n\n\n"
        "def test_unrelated():\n    assert 1 == 1\n\n\n"
        "class TestBoundaries:\n    def test_zero(self):\n        assert apply_discount(100, 0) == 100\n"
    ),
    "tests/test_web.py": "from shop import web\n\n\ndef test_import():\n    assert web\n",
    "pytest.ini": "[pytest]\n",
    "tests/conftest.py": "import pytest\n\n\n@pytest.fixture\ndef cart():\n    return []\n",
}
CHANGE = (
    "diff --git a/shop/pricing.py b/shop/pricing.py\n--- a/shop/pricing.py\n+++ b/shop/pricing.py\n"
    "@@ -1,2 +1,2 @@\n def apply_discount(price, pct):\n-    return price - price * pct // 100\n"
    "+    return price - min(price * pct // 100, price // 2)\n"
)


@pytest.fixture
def shop(tmp_path):
    write_files(tmp_path, SHOP)
    return tmp_path


def _summaries(repo):
    return [summarize(c, repo) for c in parse_diff(CHANGE)]


# ---- existing tests -------------------------------------------------------------------------
def test_tests_that_call_the_changed_function_are_direct(shop):
    found = {
        t.nodeid: t
        for t in testscan.match_existing(_summaries(shop), testscan.scan_test_files(shop))
    }
    assert found["tests/test_pricing.py::test_ten_percent"].strength == "direct"
    assert found["tests/test_pricing.py::TestBoundaries::test_zero"].strength == "direct"
    assert "apply_discount" in found["tests/test_pricing.py::test_ten_percent"].symbols


def test_a_test_that_only_imports_the_module_is_weaker(shop):
    found = {
        t.nodeid: t
        for t in testscan.match_existing(_summaries(shop), testscan.scan_test_files(shop))
    }
    assert found["tests/test_pricing.py::test_unrelated"].strength == "module"


def test_tests_for_other_modules_are_not_linked(shop):
    found = testscan.match_existing(_summaries(shop), testscan.scan_test_files(shop))
    assert not any("test_web" in t.nodeid for t in found)


def test_quarantined_tests_are_flagged(shop):
    entries = ["tests/test_pricing.py::test_ten_percent"]
    found = {
        t.nodeid: t
        for t in testscan.match_existing(_summaries(shop), testscan.scan_test_files(shop), entries)
    }
    assert found["tests/test_pricing.py::test_ten_percent"].quarantined
    assert not found["tests/test_pricing.py::TestBoundaries::test_zero"].quarantined


@pytest.mark.parametrize(
    "nodeid,entries,expected",
    [
        ("t.py::test_a", ["t.py::test_a"], True),
        ("t.py::test_a[1-2]", ["t.py::test_a"], True),  # the entry names the unparametrised test
        ("t.py::test_a", ["t.py::test_a[1-2]"], True),
        ("t.py::test_ab", ["t.py::test_a"], False),
        ("t.py::TestX::test_a", ["t.py::TestX::*"], True),
        ("u.py::test_a", ["t.py::*"], False),
    ],
)
def test_is_quarantined(nodeid, entries, expected):
    assert testscan.is_quarantined(nodeid, entries) is expected


def test_quarantine_file_in_the_flaky_quarantine_format(tmp_path):
    path = tmp_path / "quarantine.json"
    path.write_text(json.dumps({"tests": {"t.py::test_b": {}, "t.py::test_a": {}}}))
    assert testscan.load_quarantine(path) == ["t.py::test_a", "t.py::test_b"]


@pytest.mark.parametrize("content", ["not json", "[]", '{"tests": []}', ""])
def test_unreadable_quarantine_files_give_an_empty_list(tmp_path, content):
    path = tmp_path / "q.json"
    path.write_text(content)
    assert testscan.load_quarantine(path) == []
    assert testscan.load_quarantine(tmp_path / "missing.json") == []
    assert testscan.load_quarantine(None) == []


# ---- import graph ---------------------------------------------------------------------------
def test_dependents_are_found_up_to_two_hops_and_exclude_tests(shop):
    graph = importgraph.build_graph(shop)
    found = {d.module: d for d in importgraph.dependents(_summaries(shop), graph)}
    assert found["shop.api"].hops == 1
    assert found["shop.web"].hops == 2 and found["shop.web"].via == "shop.api"
    assert not any(m.startswith("tests") for m in found)


def test_one_hop_limit(shop):
    graph = importgraph.build_graph(shop)
    found = importgraph.dependents(_summaries(shop), graph, max_hops=1)
    assert [d.module for d in found] == ["shop.api"]


def test_unittest_classes_are_found_whatever_their_name(shop):
    write_files(
        shop,
        {
            "tests/test_totals.py": (
                "import unittest\nfrom shop.pricing import apply_discount\n\n\n"
                "class PricingTests(unittest.TestCase):\n    def test_half(self):\n"
                "        self.assertEqual(apply_discount(100, 50), 50)\n"
            )
        },
    )
    found = testscan.match_existing(_summaries(shop), testscan.scan_test_files(shop))
    by_id = {t.nodeid: t for t in found}
    assert by_id["tests/test_totals.py::PricingTests::test_half"].strength == testscan.DIRECT


# ---- git history ----------------------------------------------------------------------------
@needs_git
def test_history_counts_churn_and_bug_fixes_as_of_a_date(tmp_path):
    write_files(tmp_path, {"a.py": "x = 1\n"})
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "feat: add a", date="2026-01-10T10:00:00")
    for n, (msg, when) in enumerate(
        [("fix: crash on empty", "2026-08-10T10:00:00"), ("tweak", "2026-08-20T10:00:00")], start=2
    ):
        (tmp_path / "a.py").write_text(f"x = {n}\n")
        git(tmp_path, "commit", "-qam", msg, date=when)
    (tmp_path / "a.py").write_text("x = 99\n")
    git(tmp_path, "commit", "-qam", "fix: after the as-of date", date="2026-09-15T10:00:00")

    hist = gitsignals.file_history(tmp_path, ["a.py"], date(2026, 9, 1))["a.py"]
    assert hist.commits_90d == 2  # the January commit is too old, the September one too new
    assert hist.bugfixes_365d == 1  # only the August fix: "feat" is not a fix, September is too new
    assert hist.last_change == "2026-08-20"
    assert hist.bugfix_subjects == ["fix: crash on empty"]


@needs_git
def test_a_file_with_no_history_is_empty_not_an_error(tmp_path):
    write_files(tmp_path, {"a.py": "x = 1\n"})
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init", date="2026-08-01T10:00:00")
    hist = gitsignals.file_history(tmp_path, ["nope.py"], date(2026, 9, 1))["nope.py"]
    assert hist.last_change is None and hist.commits_90d == 0


@needs_git
def test_git_does_not_read_configuration_from_the_analysed_repository(tmp_path):
    write_files(tmp_path, {"a.py": "x = 1\n", ".gitconfig": "[probe]\n\tfrom = repo\n"})
    git(tmp_path, "init", "-q")
    assert gitsignals._git(tmp_path, "config", "--get", "probe.from") is None
    assert gitsignals.is_git_repo(tmp_path) and not gitsignals.is_git_repo(tmp_path / ".git")


def test_a_plain_directory_is_not_a_git_repo(tmp_path):
    assert gitsignals.is_git_repo(tmp_path) is False


# ---- coverage -------------------------------------------------------------------------------
COBERTURA = """<?xml version="1.0" ?>
<coverage><sources><source>/repo</source></sources><packages><package><classes>
<class filename="shop/pricing.py"><lines>
<line number="1" hits="4"/><line number="2" hits="0"/><line number="3" hits="0"/>
</lines></class></classes></package></packages></coverage>
"""


def test_cobertura_reports_lines_that_never_ran(tmp_path):
    path = tmp_path / "coverage.xml"
    path.write_text(COBERTURA)
    cov = coverage.load_cobertura(path)
    assert coverage.uncovered(cov, "shop/pricing.py", {1, 2, 3, 9}) == {2, 3}
    assert coverage.uncovered(cov, "other.py", {1}) is None


def test_an_exact_coverage_name_wins_over_a_shorter_one_that_also_matches():
    cov = {"utils.py": {3: 0}, "shop/utils.py": {3: 1}, "/ci/other/shop/utils.py": {3: 0}}
    assert coverage.uncovered(cov, "shop/utils.py", {3}) == set()
    assert (
        coverage.uncovered({"utils.py": {3: 0}, "x/shop/utils.py": {3: 1}}, "shop/utils.py", {3})
        == set()
    )


@pytest.mark.parametrize(
    "text",
    [
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><coverage/>',
        "<coverage><unclosed>",
        "<coverage></coverage>",
    ],
)
def test_bad_coverage_files_are_refused(tmp_path, text):
    path = tmp_path / "coverage.xml"
    path.write_text(text)
    with pytest.raises(coverage.CoverageError):
        coverage.load_cobertura(path)


# ---- environment ----------------------------------------------------------------------------
def test_environment_detection_reads_fixtures_and_frameworks(shop):
    env = environment.detect(shop)
    assert "pytest" in env["frameworks"]
    assert any("cart" in str(f) for f in env["fixtures"])


def test_environment_detection_ignores_virtualenvs(tmp_path):
    write_files(tmp_path, {".venv/lib/pkg/conftest.py": "import pytest\n", "app.py": "x = 1\n"})
    assert environment.detect(tmp_path)["frameworks"] == []
