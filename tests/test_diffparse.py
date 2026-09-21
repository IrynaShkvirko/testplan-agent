import pytest

from testplan_agent.diffparse import DiffError, parse_diff

MODIFIED = """diff --git a/app/a.py b/app/a.py
index 111..222 100644
--- a/app/a.py
+++ b/app/a.py
@@ -1,4 +1,5 @@
 one
-two
+TWO
+two and a half
 three
 four
@@ -10,3 +11,3 @@ def later():
 ten
-eleven
+ELEVEN
 twelve
"""


def test_modified_file_line_numbers_follow_the_new_file():
    (change,) = parse_diff(MODIFIED)
    assert change.path == "app/a.py"
    assert change.status == "modified"
    assert change.added == 3 and change.removed == 2
    assert change.added_line_numbers() == [2, 3, 12]
    assert change.removed_line_numbers() == [2, 11]
    assert change.hunks[1].section == "def later():"


def test_touched_lines_include_the_line_after_a_pure_deletion():
    (change,) = parse_diff("--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,2 @@\n a\n-b\n c\n")
    assert change.touched_new_lines() == [2]


def test_hunk_text_keeps_context_but_added_text_does_not():
    (change,) = parse_diff(MODIFIED)
    assert "three" in change.hunk_text()
    assert "three" not in change.added_text()
    assert change.removed_text().splitlines() == ["two", "eleven"]


def test_added_deleted_and_renamed_files():
    text = (
        "diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n"
        "@@ -0,0 +1,2 @@\n+a\n+b\n"
        "diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n+++ /dev/null\n"
        "@@ -1,1 +0,0 @@\n-gone\n"
        "diff --git a/p.py b/q.py\nsimilarity index 90%\nrename from p.py\nrename to q.py\n"
        "--- a/p.py\n+++ b/q.py\n@@ -1,1 +1,1 @@\n-x\n+y\n"
    )
    added, deleted, renamed = parse_diff(text)
    assert (added.path, added.status) == ("new.py", "added")
    assert (deleted.path, deleted.status) == ("old.py", "deleted")
    assert (renamed.path, renamed.old_path, renamed.status) == ("q.py", "p.py", "renamed")


def test_binary_files_are_kept_without_hunks():
    (change,) = parse_diff("diff --git a/i.png b/i.png\nBinary files a/i.png and b/i.png differ\n")
    assert change.binary and change.hunks == []


def test_plain_diff_u_output_without_git_headers():
    (change,) = parse_diff(
        "--- a/x.txt\t2026-01-01\n+++ b/x.txt\t2026-01-02\n@@ -1 +1 @@\n-a\n+b\n"
    )
    assert change.path == "x.txt"
    assert change.added == 1 and change.removed == 1


def test_no_newline_marker_is_not_counted_as_a_line():
    (change,) = parse_diff("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n")
    assert change.added == 1 and change.removed == 1


@pytest.mark.parametrize("text", ["", "hello world\n", "just some prose\nwith lines\n"])
def test_text_that_is_not_a_diff_is_rejected(text):
    with pytest.raises(DiffError):
        parse_diff(text)
