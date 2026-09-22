"""A page for reviewing the evaluation cases and their defect labels before they are trusted.

    python -m evals.review            # writes evals/runs/review.html

Shows every case: its story, its full diff, and each known-defect label with the line it points
at, what running its trigger showed, who drafted it and whether a person has verified it.
Real (git) cases are fetched through the local cache to show their diff.
"""

from __future__ import annotations

import html
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from testplan_agent.diffparse import parse_diff

from .cases import CASES_DIR, Case, checkout_git_case, load_cases
from .run import FLOW_DIR, _demo_repo

CSS = """
:root { --bg: #fbfaf8; --fg: #1d1d1f; --muted: #6b6b70; --line: #e4e2dd; --card: #ffffff;
  --add: #e7f5ea; --del: #fbeaea; --warn: #9a5b00; --ok: #1f7a3a; --code: #f4f2ee; }
@media (prefers-color-scheme: dark) { :root { --bg: #161618; --fg: #ececee; --muted: #9d9da3;
  --line: #2c2c30; --card: #1e1e21; --add: #17301e; --del: #3a1c1c; --warn: #f0b35a;
  --ok: #6fd08c; --code: #242428; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 -apple-system, "Segoe UI", system-ui, sans-serif; }
main { max-width: 1060px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 24px; margin: 0 0 4px; } h2 { font-size: 18px; margin: 0; }
.muted { color: var(--muted); }
.case { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 18px; margin: 18px 0; }
.chips span { display: inline-block; font-size: 12px; border: 1px solid var(--line);
  border-radius: 99px; padding: 1px 8px; margin-right: 4px; color: var(--muted); }
details { margin-top: 10px; } summary { cursor: pointer; color: var(--muted); }
pre { background: var(--code); border-radius: 8px; padding: 10px; overflow-x: auto;
  font: 12.5px/1.45 ui-monospace, Menlo, monospace; margin: 8px 0 0; }
.diff .a { background: var(--add); display: block; } .diff .d { background: var(--del); display: block; }
table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }
th, td { text-align: left; vertical-align: top; padding: 8px; border-top: 1px solid var(--line); }
th { width: 120px; color: var(--muted); font-weight: 500; }
code { font: 12.5px ui-monospace, Menlo, monospace; background: var(--code); padding: 1px 4px;
  border-radius: 4px; }
.todo { color: var(--warn); font-weight: 600; } .done { color: var(--ok); font-weight: 600; }
.defect { margin-top: 14px; border-top: 2px solid var(--line); padding-top: 6px; }
.summary { display: flex; gap: 24px; flex-wrap: wrap; margin: 12px 0 4px; }
.summary b { font-size: 22px; display: block; }
"""


def _diff_html(diff: str) -> str:
    out = []
    for line in diff.splitlines():
        cls = (
            "a"
            if line.startswith("+") and not line.startswith("+++")
            else ("d" if line.startswith("-") and not line.startswith("---") else "")
        )
        text = html.escape(line) or " "
        out.append(f'<span class="{cls}">{text}</span>' if cls else text)
    return '<pre class="diff">' + "\n".join(out) + "</pre>"


def _line_text(diff: str, location: str) -> Optional[str]:
    path, line = location.rsplit(":", 1)
    for change in parse_diff(diff):
        if change.path == path:
            for hunk in change.hunks:
                for number, text in hunk.added_lines():
                    if number == int(line):
                        return text
    return None


def _case_html(case: Case, diff: str, story: str) -> str:
    repo = case.repo
    if repo["kind"] == "git":
        base = repo["url"].removesuffix(".git")
        source = (f'<a href="{html.escape(base)}/commit/{repo["commit"]}">{html.escape(base)} @ '
                  f'{repo["commit"][:10]}</a>')  # fmt: skip
        if repo.get("fixed_by"):
            source += (f' · fixed by <a href="{html.escape(base)}/commit/{repo["fixed_by"]}">'
                       f'{repo["fixed_by"][:10]}</a>')  # fmt: skip
    else:
        source = "demo shop (synthetic)"
    parts = [
        f'<section class="case" id="{html.escape(case.id)}">',
        f"<h2>{html.escape(case.id)} · {html.escape(case.title)}</h2>",
        f'<div class="muted">{source} · as of {case.as_of}</div>',
        '<div class="chips">'
        + "".join(f"<span>{html.escape(t)}</span>" for t in case.tags)
        + "</div>",
    ]
    if case.notes:
        parts.append(f"<p>{html.escape(case.notes)}</p>")
    parts.append(f"<details><summary>Story</summary><pre>{html.escape(story)}</pre></details>")
    parts.append(f"<details><summary>Diff</summary>{_diff_html(diff)}</details>")
    for d in case.defects:
        code = _line_text(diff, d.location)
        status = (f'<span class="done">verified by {html.escape(d.verified_by)}</span>'
                  if d.verified_by else '<span class="todo">not verified yet</span>')  # fmt: skip
        rows = [
            ("Defect", html.escape(d.summary)),
            (
                "Where",
                f"<code>{html.escape(d.location)}</code>"
                + (f"<pre>{html.escape(code.strip())}</pre>" if code else ""),
            ),
            ("Trigger", html.escape(d.trigger)),
            ("Caught if", html.escape(d.caught_if)),
            ("Observed", html.escape(d.observed) or '<span class="muted">not run</span>'),
            ("Label", f"drafted by {html.escape(d.written_by)} · {status}"),
        ]
        body = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
        parts.append(f'<div class="defect"><b>{html.escape(d.id)}</b><table>{body}</table></div>')
    parts.append("</section>")
    return "\n".join(parts)


def build(cases: List[Case], out: Path) -> Path:
    workdir = Path(tempfile.mkdtemp(prefix="testplan-review-"))
    try:
        texts: Dict[str, Dict[str, str]] = {}
        demo = _demo_repo(workdir) if any(c.repo["kind"] == "demo" for c in cases) else None
        for case in cases:
            if case.repo["kind"] == "git":
                got = checkout_git_case(case, workdir)
                texts[case.id] = {"diff": got["diff"], "story": got["story"]}
            else:
                assert demo is not None
                texts[case.id] = case.inputs(demo[1])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    labels = [d for c in cases for d in c.defects]
    unverified = sum(not d.verified_by for d in labels)
    real = sum(c.repo["kind"] == "git" for c in cases)
    head = (
        "<h1>Evaluation cases for review</h1>"
        '<p class="muted">For each defect label, check that the defect is really in the code '
        "at that line, that the trigger exposes it, and that “caught if” describes a test that "
        "would catch it without needing to know the answer. The observations come from running "
        "each trigger against the code.</p>"
        '<div class="summary">'
        f"<div><b>{len(cases)}</b>cases ({len(cases) - real} synthetic, {real} real)</div>"
        f"<div><b>{len(labels)}</b>defect labels</div>"
        f'<div><b class="{"todo" if unverified else "done"}">{unverified}</b>not verified yet</div>'
        "</div>"
    )
    toc = "<p>" + " · ".join(f'<a href="#{c.id}">{html.escape(c.id)}</a>' for c in cases) + "</p>"
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Eval case review</title><style>{CSS}</style></head><body><main>"
        + head
        + toc
        + "\n".join(_case_html(c, texts[c.id]["diff"], texts[c.id]["story"]) for c in cases)
        + "</main></body></html>\n"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    out = build(load_cases(CASES_DIR), FLOW_DIR / "review.html")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
