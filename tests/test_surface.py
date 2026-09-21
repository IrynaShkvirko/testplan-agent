import pytest
from conftest import write_files

from testplan_agent.diffparse import parse_diff
from testplan_agent.surface import after_text, classify_path, summarize


@pytest.mark.parametrize(
    "path,kind",
    [
        ("src/app/service.py", "source"),
        ("tests/test_service.py", "test"),
        ("app/service_test.py", "test"),
        ("migrations/0004_add_column.sql", "migration"),
        ("db/migrate/20260101_x.rb", "migration"),
        ("requirements.txt", "dependency"),
        ("package.json", "dependency"),
        (".github/workflows/ci.yml", "ci"),
        ("docs/guide.md", "docs"),
        ("README.md", "docs"),
        ("settings.toml", "config"),
    ],
)
def test_classify_path(path, kind):
    assert classify_path(path) == kind


OLD = "def price(a, b):\n    return a - b\n\n\ndef other():\n    return 1\n"
NEW = "def price(a, b, c=0):\n    return a - b - c\n\n\ndef other():\n    return 1\n"
DIFF = (
    "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1,3 +1,3 @@\n"
    "-def price(a, b):\n-    return a - b\n+def price(a, b, c=0):\n+    return a - b - c\n \n"
)


def _change():
    return parse_diff(DIFF)[0]


def test_after_text_when_the_checkout_is_at_the_base_revision():
    text, how = after_text(_change(), OLD)
    assert how == "applied" and text == NEW


def test_after_text_when_the_checkout_already_has_the_change():
    text, how = after_text(_change(), NEW)
    assert how == "checkout-has-change" and text == NEW


def test_after_text_when_the_checkout_matches_neither():
    text, how = after_text(_change(), "something else entirely\n" * 6)
    assert (text, how) == (None, "unknown")
    assert after_text(_change(), None) == (None, "unknown")


def test_after_text_for_a_new_file_needs_no_checkout():
    change = parse_diff("--- /dev/null\n+++ b/n.py\n@@ -0,0 +1,2 @@\n+a = 1\n+b = 2\n")[0]
    assert after_text(change, None) == ("a = 1\nb = 2\n", "new-file")


def test_summary_reports_signature_changes_and_line_counts(tmp_path):
    write_files(tmp_path, {"m.py": OLD})
    summary = summarize(_change(), tmp_path)
    (sym,) = [s for s in summary.symbols if s.qualname == "price"]
    assert sym.status == "modified"
    assert sym.signature_change == "(a, b) -> (a, b, c=0)"
    assert summary.line_count == len(NEW.splitlines())


@pytest.mark.parametrize(
    "decorator,expected",
    [
        ("@app.post('/refunds')", "POST /refunds"),
        ("@router.delete('/orders/{id}')", "DELETE /orders/{id}"),
        ("@app.route('/refunds', methods=['PUT'])", "PUT /refunds"),
        ("@app.route('/health')", "GET /health"),
    ],
)
def test_summary_finds_http_endpoints_from_decorators(tmp_path, decorator, expected):
    src = f"{decorator}\ndef handler():\n    return 1\n"
    diff = "--- /dev/null\n+++ b/api.py\n@@ -0,0 +1,3 @@\n" + "".join(
        f"+{x}\n" for x in src.splitlines()
    )
    summary = summarize(parse_diff(diff)[0], tmp_path)
    (sym,) = [s for s in summary.symbols if s.qualname == "handler"]
    assert sym.status == "added"
    assert sym.endpoint == expected


def test_a_plain_function_is_not_an_endpoint(tmp_path):
    diff = "--- /dev/null\n+++ b/lib.py\n@@ -0,0 +1,2 @@\n+def helper():\n+    return 1\n"
    (sym,) = [s for s in summarize(parse_diff(diff)[0], tmp_path).symbols if s.qualname == "helper"]
    assert sym.endpoint is None


def test_sensitive_areas_are_found_by_word(tmp_path):
    diff = (
        "--- /dev/null\n+++ b/pay.py\n@@ -0,0 +1,2 @@\n"
        "+def charge(order):\n+    return order.price * 2  # refund rules live elsewhere\n"
    )
    summary = summarize(parse_diff(diff)[0], tmp_path)
    assert "money" in summary.areas
