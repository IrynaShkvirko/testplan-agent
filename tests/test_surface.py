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
        ("config/app.json", "config"),
        ("demo/plans/discount-cap.json", "data"),
        ("examples/settings.yml", "data"),
        ("data/prices.csv", "data"),
        ("exports/rows.jsonl", "data"),
        ("demo/run_demo.py", "source"),
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


@pytest.mark.parametrize("name", ["apply_discount(order_total)", "applyDiscount(orderTotal)"])
def test_sensitive_areas_are_found_inside_identifiers(tmp_path, name):
    diff = f"--- /dev/null\n+++ b/lib.py\n@@ -0,0 +1,2 @@\n+def {name}:\n+    return 1\n"
    summary = summarize(parse_diff(diff)[0], tmp_path)
    assert set(summary.areas["money"]) >= {"discount", "total"}


RENAME_OLD = "def total(x):\n    return x\n"
RENAME_DIFF = (
    "--- a/m.py\n+++ b/m.py\n@@ -1,2 +1,2 @@\n-def total(x):\n+def get_total(x):\n     return x\n"
)


@pytest.mark.parametrize("checkout", [RENAME_OLD, None])
def test_a_removed_function_is_not_hidden_by_a_name_ending_the_same_way(tmp_path, checkout):
    if checkout is not None:
        write_files(tmp_path, {"m.py": checkout})
    summary = summarize(parse_diff(RENAME_DIFF)[0], tmp_path)
    status = {s.qualname: s.status for s in summary.symbols}
    assert status == {"get_total": "added", "total": "removed"}


MULTI_OLD = "def price(\n    a,\n    b,\n):\n    return a - b\n"
MULTI_NEW = "def price(\n    a,\n    b,\n    c=0,\n):\n    return a - b - c\n"
MULTI_DIFF = (
    "--- a/m.py\n+++ b/m.py\n@@ -1,5 +1,6 @@\n def price(\n     a,\n     b,\n+    c=0,\n ):\n"
    "-    return a - b\n+    return a - b - c\n"
)


@pytest.mark.parametrize("checkout", [MULTI_OLD, MULTI_NEW])
def test_a_signature_written_over_several_lines_is_compared(tmp_path, checkout):
    write_files(tmp_path, {"m.py": checkout})
    (sym,) = summarize(parse_diff(MULTI_DIFF)[0], tmp_path).symbols
    assert (sym.qualname, sym.status) == ("price", "modified")
    assert sym.signature_change == "(a, b) -> (a, b, c=0)"


def test_methods_are_matched_by_class_not_by_bare_name(tmp_path):
    old = "class A:\n    def save(self):\n        return 1\n\n\nclass B:\n    def load(self):\n        return 2\n"
    diff = (
        "--- a/m.py\n+++ b/m.py\n@@ -7,2 +7,2 @@ class B:\n-    def load(self):\n"
        "+    def save(self):\n         return 2\n"
    )
    write_files(tmp_path, {"m.py": old})
    status = {s.qualname: s.status for s in summarize(parse_diff(diff)[0], tmp_path).symbols}
    assert status == {"B.save": "added", "B.load": "removed"}


def test_data_files_are_not_scanned_for_sensitive_areas(tmp_path):
    diff = '--- /dev/null\n+++ b/demo/plans/p.json\n@@ -0,0 +1 @@\n+{"price": 1, "discount": 2}\n'
    summary = summarize(parse_diff(diff)[0], tmp_path)
    assert summary.kind == "data" and summary.areas == {}
