"""The command line, including exit codes: 0 ok, 1 unresolved plan errors, 2 usage errors."""

from __future__ import annotations

import json

import pytest
from conftest import AS_OF

from testplan_agent.cli import main


def paths(demo, name="discount-cap"):
    return [
        "--diff", str(demo.changes / name / "change.patch"),
        "--spec", str(demo.changes / name / "story.md"),
        "--repo", str(demo.repo),
        "--as-of", AS_OF,
    ]  # fmt: skip


def test_generate_writes_markdown_and_json(demo, tmp_path, capsys):
    md, js = tmp_path / "plan.md", tmp_path / "plan.json"
    code = main(["generate", *paths(demo), "-o", str(md), "--json", str(js)])
    assert code == 0
    assert md.read_text().startswith("# Test plan:")
    plan = json.loads(js.read_text())
    assert plan["schema_version"] == 1 and plan["cases"]
    assert "0 error(s)" in capsys.readouterr().err


def test_generate_prints_markdown_to_stdout_by_default(demo, capsys):
    assert main(["generate", *paths(demo)]) == 0
    assert capsys.readouterr().out.startswith("# Test plan:")


def test_context_then_generate_from_the_saved_bundle(demo, tmp_path):
    ctx = tmp_path / "ctx.json"
    assert main(["context", *paths(demo), "-o", str(ctx)]) == 0
    direct, viaCtx = tmp_path / "a.md", tmp_path / "b.md"
    assert main(["generate", *paths(demo), "-o", str(direct)]) == 0
    assert main(["generate", "--context", str(ctx), "-o", str(viaCtx)]) == 0
    assert direct.read_text() == viaCtx.read_text()


def test_show_context_prints_the_exact_prompts_and_calls_no_model(demo, capsys):
    assert main(["generate", *paths(demo), "--show-context"]) == 0
    out = capsys.readouterr().out
    assert "=== system ===" in out and "=== user ===" in out and "<untrusted-context>" in out


def test_validate_accepts_a_good_plan_and_rejects_a_bad_one(demo, tmp_path, capsys):
    ctx, js = tmp_path / "ctx.json", tmp_path / "plan.json"
    main(
        [
            "generate",
            *paths(demo),
            "--save-context",
            str(ctx),
            "--json",
            str(js),
            "-o",
            str(tmp_path / "p.md"),
        ]
    )
    capsys.readouterr()
    assert main(["validate", str(js), "--context", str(ctx), "--repo", str(demo.repo)]) == 0
    assert capsys.readouterr().out.strip().endswith("OK")

    plan = json.loads(js.read_text())
    plan["cases"][0]["evidence"].append("F999")
    js.write_text(json.dumps(plan))
    assert main(["validate", str(js), "--context", str(ctx)]) == 1
    assert "unknown_fact" in capsys.readouterr().out


def test_a_scripted_client_that_never_produces_a_valid_plan_exits_1_or_2(demo, tmp_path, capsys):
    plan_path = tmp_path / "plan.json"
    main(["generate", *paths(demo), "--json", str(plan_path), "-o", str(tmp_path / "x.md")])
    plan = json.loads(plan_path.read_text())
    plan["cases"][0]["evidence"].append("F999")
    replies = tmp_path / "replies.json"
    replies.write_text(json.dumps(plan))
    code = main(
        [
            "generate",
            *paths(demo),
            "--client",
            "scripted",
            "--responses",
            str(replies),
            "-o",
            str(tmp_path / "y.md"),
        ]
    )
    assert code == 1  # a plan exists, but with unresolved errors; it is written and flagged
    assert "unknown_fact" in (tmp_path / "y.md").read_text()

    replies.write_text('"not a plan"')
    assert (
        main(["generate", *paths(demo), "--client", "scripted", "--responses", str(replies)]) == 2
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["generate"],
        ["generate", "--spec", "nothing.md"],
        ["generate", "--diff", "missing.patch"],
        ["context", "--diff", "-", "--as-of", "yesterday"],
        ["generate", "--client", "scripted"],
        ["validate", "missing.json", "--context", "missing.json"],
    ],
)
def test_usage_errors_exit_2_with_a_message(argv, capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.stdin", __import__("io").StringIO("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n")
    )
    assert main(argv) == 2
    assert capsys.readouterr().err.startswith("error:")


def test_an_unknown_test_level_is_a_usage_error(demo, capsys):
    assert main(["generate", *paths(demo), "--levels", "unit,smoke"]) == 2
    assert "smoke" in capsys.readouterr().err


def test_levels_constrain_the_plan(demo, tmp_path):
    js = tmp_path / "plan.json"
    assert (
        main(
            [
                "generate",
                *paths(demo),
                "--levels",
                "unit",
                "--json",
                str(js),
                "-o",
                str(tmp_path / "p.md"),
            ]
        )
        == 0
    )
    assert {c["level"] for c in json.loads(js.read_text())["cases"]} == {"unit"}


def test_a_diff_can_come_from_stdin(demo, monkeypatch, capsys):
    import io

    diff = (demo.changes / "discount-cap" / "change.patch").read_text()
    monkeypatch.setattr("sys.stdin", io.StringIO(diff))
    assert main(["generate", "--diff", "-", "--repo", str(demo.repo), "--as-of", AS_OF]) == 0
    assert "# Test plan" in capsys.readouterr().out


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_a_docs_only_change_with_criteria_gets_a_clean_plan(tmp_path, capsys):
    (tmp_path / "d.patch").write_text("--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n")
    (tmp_path / "s.md").write_text(
        "# Docs\n\n## Acceptance criteria\n- AC-1: The README must say how to install\n"
    )
    argv = ["generate", "--diff", str(tmp_path / "d.patch"), "--spec", str(tmp_path / "s.md")]
    assert main([*argv, "-o", str(tmp_path / "p.md")]) == 0
    assert "after 1 attempt(s)" in capsys.readouterr().err


@pytest.mark.parametrize(
    "bundle",
    ['{"version": 1, "changes": [{"path": "x"}]}', '{"version": 1, "facts": [{}]}', "[1]"],
)
def test_a_malformed_saved_bundle_is_a_usage_error(tmp_path, capsys, bundle):
    (tmp_path / "b.json").write_text(bundle)
    assert main(["generate", "--context", str(tmp_path / "b.json")]) == 2
    assert capsys.readouterr().err.startswith("error:")


def test_a_planner_that_gives_no_answer_is_reported_not_raised(demo, capsys, monkeypatch):
    from testplan_agent.llm import HeuristicClient, LLMError

    def fail(self, system, user):
        raise LLMError("service unavailable")

    monkeypatch.setattr(HeuristicClient, "complete", fail)
    assert main(["generate", *paths(demo)]) == 2
    assert "service unavailable" in capsys.readouterr().err


def test_model_usage_is_reported_even_when_no_plan_comes_back(demo, capsys, monkeypatch):
    from testplan_agent.llm import Completion, HeuristicClient, Usage

    def garbage(self, system, turns):
        return Completion("not json", Usage(input_tokens=5000, output_tokens=100, cost_usd=0.03))

    monkeypatch.setattr(HeuristicClient, "complete", garbage)
    assert main(["generate", *paths(demo)]) == 2
    err = capsys.readouterr().err
    assert "model usage: 5,000 tokens in, 100 out" in err and "about $0.03" in err
