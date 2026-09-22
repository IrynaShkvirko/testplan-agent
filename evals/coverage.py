"""Coverage of known defects: graded by a person on a worksheet, then merged into the results.

    python -m evals.coverage worksheet --variant baseline      # writes <variant>/worksheet.html
    python -m evals.coverage apply --variant baseline grades.json
    python -m evals.coverage status --variant baseline

For every plan and every known defect of its case, the grader picks one of:

- caught:  a condition in the plan, run as written with sensible data, exercises the defect's
           trigger (or an equivalent input) and checks the behaviour the defect breaks;
- partial: a condition targets the right behaviour or criterion but never forces the input
           that exposes the defect, so running it may or may not catch it;
- missed:  nothing in the plan would expose it.

A plan with no test conditions is graded missed without asking. Grades live in
``<variant>/coverage.json`` and become two metrics per case: ``coverage`` (share of its defects
caught) and ``coverage_lenient`` (partial counts half). A case is scored only once all of its
defects are graded.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .cases import CASES_DIR, Case, load_cases
from .run import FLOW_DIR

GRADES = {"caught": 1.0, "partial": 0.5, "missed": 0.0}
HINT_LINES = 10  # a condition citing the defect's file within this many lines is highlighted
AUTO = "auto: the plan has no test conditions"


class CoverageError(ValueError):
    pass


# ---- reading runs -----------------------------------------------------------------------------
def _rows(vdir: Path) -> List[Dict[str, Any]]:
    path = vdir / "results.jsonl"
    if not path.is_file():
        raise CoverageError(f"no results in {vdir}; run the variant first")
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _key(case_id: str, rep: int, defect_id: str) -> str:
    return f"{case_id}/{rep}/{defect_id}"


def gradable(vdir: Path, cases: List[Case]) -> List[Tuple[Dict[str, Any], Case, Dict[str, Any]]]:
    """(row, case, plan) for every graded-ok row whose case has known defects."""
    by_id = {c.id: c for c in cases}
    out = []
    for row in _rows(vdir):
        case = by_id.get(row["prompt_id"])
        if row.get("status") != "ok" or case is None or not case.defects:
            continue
        plan = json.loads((vdir / row["meta"]["plan"]).read_text(encoding="utf-8"))
        out.append((row, case, plan))
    return sorted(out, key=lambda t: (t[1].id, t[0]["rep"]))


def auto_grades(items) -> Dict[str, Dict[str, str]]:
    """Grades that need no person: every defect of a plan with no conditions is missed."""
    grades = {}
    for row, case, plan in items:
        if not plan.get("cases"):
            for d in case.defects:
                grades[_key(case.id, row["rep"], d.id)] = {
                    "grade": "missed",
                    "by": "",
                    "note": AUTO,
                }
    return grades


def cites_near(condition: Dict[str, Any], location: str) -> bool:
    path, line = location.rsplit(":", 1)
    for ref in condition.get("evidence", []):
        if ":" not in ref or not ref.startswith(path + ":"):
            continue
        span = ref.rsplit(":", 1)[1].split("-")
        try:
            lo, hi = int(span[0]), int(span[-1])
        except ValueError:
            continue
        if lo - HINT_LINES <= int(line) <= hi + HINT_LINES:
            return True
    return False


# ---- the worksheet ----------------------------------------------------------------------------
PAGE_CSS = """
:root { --bg: #fbfaf8; --fg: #1d1d1f; --muted: #6b6b70; --line: #e4e2dd; --card: #fff;
  --hint: #fff4d6; --code: #f4f2ee; --ok: #1f7a3a; --warn: #9a5b00; }
@media (prefers-color-scheme: dark) { :root { --bg: #161618; --fg: #ececee; --muted: #9d9da3;
  --line: #2c2c30; --card: #1e1e21; --hint: #3a3016; --code: #242428; --ok: #6fd08c;
  --warn: #f0b35a; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 -apple-system, "Segoe UI", system-ui, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 96px; }
.bar { position: sticky; top: 0; background: var(--bg); border-bottom: 1px solid var(--line);
  padding: 10px 16px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; z-index: 2; }
.case { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 18px; margin: 18px 0; }
h2 { font-size: 18px; margin: 0 0 6px; } .muted { color: var(--muted); }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; margin-top: 8px; }
th, td { text-align: left; vertical-align: top; padding: 6px 8px; border-top: 1px solid var(--line); }
tr.hint td { background: var(--hint); }
.defect { margin-top: 14px; padding: 12px; border: 1px solid var(--line); border-radius: 8px; }
.defect.done { border-color: var(--ok); }
.choices label { margin-right: 14px; white-space: nowrap; }
input[type=text] { width: 100%; padding: 6px; margin-top: 6px; background: var(--code);
  color: var(--fg); border: 1px solid var(--line); border-radius: 6px; }
select, button { padding: 5px 10px; }
code { font: 12.5px ui-monospace, Menlo, monospace; background: var(--code); padding: 1px 4px; }
details summary { cursor: pointer; color: var(--muted); }
ol { margin: 4px 0; padding-left: 20px; }
.auto { color: var(--muted); font-style: italic; }
"""

PAGE_JS = r"""
const KEY = "testplan-coverage-" + VARIANT;
let saved = {};
try { saved = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
function persist() { try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) {} }
function refresh() {
  let done = 0, total = 0;
  document.querySelectorAll(".defect[data-key]").forEach(el => {
    total++;
    const g = saved[el.dataset.key];
    const has = el.dataset.auto === "1" || (g && g.grade);
    el.classList.toggle("done", !!has);
    if (has) done++;
  });
  document.getElementById("progress").textContent = done + " of " + total + " graded";
}
document.querySelectorAll(".defect[data-key]").forEach(el => {
  if (el.dataset.auto === "1") return;
  const key = el.dataset.key, g = saved[key] || {};
  el.querySelectorAll("input[type=radio]").forEach(r => {
    r.checked = g.grade === r.value;
    r.addEventListener("change", () => { saved[key] = {...(saved[key] || {}), grade: r.value}; persist(); refresh(); });
  });
  const by = el.querySelector("select"), note = el.querySelector("input[type=text]");
  by.value = g.by || ""; note.value = g.note || "";
  by.addEventListener("change", () => { saved[key] = {...(saved[key] || {}), by: by.value}; persist(); });
  note.addEventListener("input", () => { saved[key] = {...(saved[key] || {}), note: note.value}; persist(); });
});
const grader = document.getElementById("grader");
grader.value = saved.__grader || "";
grader.addEventListener("input", () => { saved.__grader = grader.value; persist(); });
document.getElementById("download").addEventListener("click", () => {
  const grades = {};
  for (const [k, v] of Object.entries(saved)) if (!k.startsWith("__") && v.grade) grades[k] = v;
  const doc = {variant: VARIANT, grader: grader.value, created: new Date().toISOString().slice(0, 10), grades};
  const url = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)], {type: "application/json"}));
  const a = Object.assign(document.createElement("a"), {href: url, download: "coverage-" + VARIANT + ".json"});
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
});
refresh();
"""


def _conditions_table(plan: Dict[str, Any], hints: set) -> str:
    rows = []
    for c in plan.get("cases", []):
        steps = "".join(f"<li>{html.escape(s)}</li>" for s in c.get("steps", []))
        detail = (f"<ol>{steps}</ol>" if steps else "") + (
            f"<div><b>Expected:</b> {html.escape(c['expected'])}</div>" if c.get("expected") else ""
        )
        cls = ' class="hint"' if c["id"] in hints else ""
        rows.append(
            f"<tr{cls}><td><b>{html.escape(c['id'])}</b><br><span class='muted'>"
            f"{html.escape(c['priority'])} · {html.escape(c['technique'])}</span></td>"
            f"<td>{html.escape(c['title'])}{detail}</td>"
            f"<td><code>{html.escape(', '.join(c.get('evidence', [])))}</code></td></tr>"
        )
    if not rows:
        return "<p class='auto'>This plan has no test conditions.</p>"
    return (
        "<table><tr><th>Id</th><th>Condition</th><th>Evidence</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def worksheet(flow: Path, variant: str, cases: List[Case]) -> Path:
    vdir = flow / variant
    items = gradable(vdir, cases)
    auto = auto_grades(items)
    sections = []
    for row, case, plan in items:
        conds = plan.get("cases", [])
        options = "".join(f'<option value="{html.escape(c["id"])}">{html.escape(c["id"])}</option>'
                          for c in conds)  # fmt: skip
        defects = []
        all_hints: set = set()
        for d in case.defects:
            key = _key(case.id, row["rep"], d.id)
            hints = {c["id"] for c in conds if cites_near(c, d.location)}
            all_hints |= hints
            hint_text = (f"Conditions citing this code: {', '.join(sorted(hints))}" if hints
                         else "No condition cites this code (that alone decides nothing).")  # fmt: skip
            if key in auto:
                control = f"<p class='auto'>Missed ({AUTO[6:]}).</p>"
            else:
                radios = "".join(
                    f'<label><input type="radio" name="{html.escape(key)}" value="{g}"> {g}</label>'
                    for g in GRADES
                )
                control = (
                    f'<div class="choices">{radios} · condition: <select><option value="">-</option>'
                    f'{options}</select></div><input type="text" placeholder="note (optional)">'
                )
            defects.append(
                f'<div class="defect" data-key="{html.escape(key)}" data-auto="{int(key in auto)}">'
                f"<b>{html.escape(d.id)}</b> {html.escape(d.summary)}"
                f"<div class='muted'><code>{html.escape(d.location)}</code> · trigger: "
                f"{html.escape(d.trigger)}</div>"
                f"<div><b>Caught if:</b> {html.escape(d.caught_if)}</div>"
                f"<div class='muted'>{html.escape(hint_text)}</div>{control}</div>"
            )
        sections.append(
            f'<section class="case"><h2>{html.escape(case.id)} · rep {row["rep"]}</h2>'
            f"<div class='muted'>{html.escape(case.title)} · {len(conds)} condition(s)</div>"
            f"<details open><summary>Plan conditions</summary>{_conditions_table(plan, all_hints)}"
            f"</details>{''.join(defects)}</section>"
        )
    rubric = (
        "<p><b>caught</b>: a condition, run as written with sensible data, exercises the trigger "
        "(or an equivalent input) and checks the behaviour the defect breaks. <b>partial</b>: a "
        "condition targets the right behaviour or criterion but never forces the triggering "
        "input. <b>missed</b>: nothing would expose it. Highlighted rows cite the defect's code; "
        "a hint, not a verdict.</p>"
    )
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Coverage worksheet</title><style>{PAGE_CSS}</style></head><body>"
        '<div class="bar"><b>Coverage worksheet · ' + html.escape(variant) + "</b>"
        '<span id="progress"></span><input id="grader" type="text" placeholder="your name" '
        'style="width:180px;margin:0"><button id="download">Download grades</button></div>'
        f"<main>{rubric}{''.join(sections)}</main>"
        f"<script>const VARIANT = {json.dumps(variant)};{PAGE_JS}</script></body></html>\n"
    )
    out = vdir / "worksheet.html"
    out.write_text(page, encoding="utf-8")
    return out


# ---- merging grades ---------------------------------------------------------------------------
def load_grades(path: Path, items, variant: str) -> Dict[str, Dict[str, str]]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CoverageError(f"cannot read {path}: {exc}") from exc
    if doc.get("variant") not in (None, variant):
        raise CoverageError(f"{path} grades variant {doc['variant']!r}, not {variant!r}")
    known = {_key(case.id, row["rep"], d.id) for row, case, _ in items for d in case.defects}
    grades = {}
    for key, value in (doc.get("grades") or {}).items():
        if key not in known:
            raise CoverageError(f"{key} is not a defect of any plan in this variant")
        if value.get("grade") not in GRADES:
            raise CoverageError(f"{key}: grade must be one of {', '.join(GRADES)}")
        grades[key] = {
            "grade": value["grade"],
            "by": value.get("by", ""),
            "note": value.get("note", ""),
        }
    return grades


def apply(
    flow: Path,
    variant: str,
    cases: List[Case],
    grades_path: Optional[Path],
    write: bool = True,
) -> Dict[str, int]:
    """Merge grades into ``coverage.json`` and the results' ``coverage`` metrics.

    With ``write=False`` nothing is changed; only the counts are returned.
    """
    vdir = flow / variant
    items = gradable(vdir, cases)
    store_path = vdir / "coverage.json"
    store = json.loads(store_path.read_text(encoding="utf-8")) if store_path.is_file() else {}
    grades = dict(store.get("grades", {}))
    graders = list(store.get("graders", []))
    if grades_path is not None:
        doc = json.loads(grades_path.read_text(encoding="utf-8"))
        grades.update(load_grades(grades_path, items, variant))
        if doc.get("grader") and doc["grader"] not in graders:
            graders.append(doc["grader"])
    grades.update(auto_grades(items))
    store = {"variant": variant, "graders": graders, "updated": date.today().isoformat(),
             "grades": dict(sorted(grades.items()))}  # fmt: skip
    if write:
        store_path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")

    scored = 0
    by_row = {(row["prompt_id"], row["rep"]): case for row, case, _ in items}
    rows = _rows(vdir)
    for row in rows:
        case = by_row.get((row["prompt_id"], row["rep"]))
        if case is None:
            continue
        marks = [grades.get(_key(case.id, row["rep"], d.id)) for d in case.defects]
        row["grade"].pop("coverage", None)
        row["grade"].pop("coverage_lenient", None)
        if all(marks):
            values = [GRADES[m["grade"]] for m in marks]
            row["grade"]["coverage"] = round(sum(v == 1.0 for v in values) / len(values), 4)
            row["grade"]["coverage_lenient"] = round(sum(values) / len(values), 4)
            scored += 1
    if write:
        (vdir / "results.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
        )
    total = sum(len(case.defects) for _, case, _ in items)
    return {"graded": len(grades), "defects": total, "plans_scored": scored, "plans": len(items)}


# ---- the command ------------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m evals.coverage", description=__doc__.splitlines()[0]
    )
    p.add_argument("action", choices=("worksheet", "apply", "status"))
    p.add_argument("grades", nargs="?", type=Path, help="grades JSON from the worksheet (apply)")
    p.add_argument("--variant", required=True)
    p.add_argument("--flow", type=Path, default=FLOW_DIR)
    p.add_argument("--cases", type=Path, default=CASES_DIR)
    args = p.parse_intermixed_args(argv)  # the grades file may follow --variant
    try:
        cases = load_cases(args.cases)
        if args.action == "worksheet":
            print(f"wrote {worksheet(args.flow, args.variant, cases)}")
        else:
            if args.action == "apply" and args.grades is None:
                raise CoverageError("apply needs the grades file downloaded from the worksheet")
            applying = args.action == "apply"
            counts = apply(
                args.flow, args.variant, cases, args.grades if applying else None, write=applying
            )
            print(f"{counts['graded']} of {counts['defects']} defect grades; "
                  f"{counts['plans_scored']} of {counts['plans']} plans scored")  # fmt: skip
    except CoverageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
