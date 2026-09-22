# Evaluation

Measures whether a planner's test plans catch **known defects**, and at what cost. The
rule-based baseline is the reference; each prompt or model change is a new variant scored on
the same cases.

The baseline and every check are free. **Anything with `--client anthropic` calls the API and
costs money**: the runner shows an estimate and asks before the first call.

## Layout

```text
evals/
  cases/<id>/case.json     a change, its story, its known defects (see cases/README.md)
  synthetic.py             generates the synthetic cases (seeded defects in the demo shop)
  review.py                the review page: python -m evals.review
  run.py                   the runner: python -m evals.run
  grading.py               grades that need no judgment
  runs/                    one folder per variant: baseline, v1, v2, ...
    _state.json            metrics, report columns, the approved harness hash
    <variant>/results.jsonl   one graded row per case and rep
    <variant>/errors.jsonl    attempts with nothing to grade, with a failure class
    <variant>/plans/          the plans (kept: grading and review read them)
    <variant>/traces/         full conversations (not committed)
    <variant>/change.md       for v1, v2, ...: what changed and why (first line = summary)
```

## Cases

12 synthetic changes to the demo shop with seeded defects, and 4 real commits from public
projects (humanize twice, jmespath, prettytable) that introduced a bug a later commit fixed:
28 defect labels in all. Every trigger was run against the code and the result recorded with the label; each label
still needs a person to verify it (`verified_by`) before coverage numbers are trusted.

## Baseline

The rule-based baseline (`evals/runs/baseline/`) is the reference every variant is compared
with. Coverage per defect (strict = caught; lenient = partial counts half):

| | Defects | Strict | Lenient |
|---|---|---|---|
| synthetic | 24 | 46% (11 caught) | 69% |
| real | 4 | 0% | 12% |
| all | 28 | 39% | 61% |

It passes every check on the first try (it cites facts by construction) but writes generic
conditions: it catches defects whose criterion can only be tested with the triggering input,
and misses almost everything the story does not spell out. On the real commits, which have no
acceptance criteria, it catches nothing; for r02 it writes no conditions at all.

These grades were made by claude-opus-5 at the maintainer's request (the plans come from the
rule-based planner, not a model), and are recorded as such in `coverage.json`.

## Running

```bash
# the reference: the rule-based baseline (free, deterministic, one rep)
python -m evals.run --variant baseline --client heuristic

# Claude with the current prompt (paid): first check the harness, then approve it once
python -m evals.run --variant v1 --client anthropic --reps 2 --approve-harness
```

Options: `--model`, `--effort`, `--reps`, `--only ID ...`, `--timeout-s` (hard limit per
case, default 900), `--concurrency` (default 4), `--yes` (skip the cost question).

- **Resume:** rerunning the same command skips every case and rep that already has a row; a
  failed attempt is not a row, so it is tried again.
- **Failures are not scores.** API errors, timeouts, crashes and answers from a model other
  than the one asked for go to `errors.jsonl`. Refusals and answers cut off at the token limit
  get a row with status `refused` / `truncated` and no scores: counted, never averaged in.
- **No fallback model** during evals: a fallback's answer would be mixed into the variant.
- **Harness approval:** paid runs refuse to start until the runner and planner code match the
  hash recorded by `--approve-harness`, so numbers from a changed harness are not compared by
  accident. Approve after reviewing the change; the approval is yours to give.
- **Retries:** the SDK retries rate limits and server errors (twice, with backoff); those retries
  are not visible per row. `attempts` counts the planner's repair rounds.

## Report

The HTML report uses the lite report builder that ships with Claude Code's `claude-api` skill
(`shared/evals/report/build-report-lite.mjs`, Node only, no dependencies):

```bash
node <skill>/shared/evals/report/build-report-lite.mjs evals/runs/
```

It writes `evals/runs/report.html` (per-variant summary, a sortable per-case table, links to
each trace) and `evals/runs/trajectory/scores.tsv`. The runner also prints a summary with 95%
intervals after every run.

## Metrics

| Metric | Meaning | Graded by |
|---|---|---|
| `coverage` | share of the case's known defects the plan would catch (headline) | a person |
| `coverage_lenient` | the same, with "partial" counting half | a person |
| `checks_clean` | the final plan passes every check | code |
| `first_try_clean` | ...and needed no repair | code |
| `citations_first` | share of the first answer's citations that resolve, before repairs | code |

## Grading coverage

```bash
python -m evals.coverage worksheet --variant baseline   # writes evals/runs/baseline/worksheet.html
python -m evals.coverage apply --variant baseline ~/Downloads/coverage-baseline.json
python -m evals.coverage status --variant baseline      # how much is graded (changes nothing)
```

The worksheet shows each plan's conditions next to each known defect of its case. For every
defect pick:

- **caught**: a condition, run as written with sensible data, exercises the defect's trigger (or
  an equivalent input) and checks the behaviour the defect breaks;
- **partial**: a condition targets the right behaviour or criterion but never forces the
  triggering input;
- **missed**: nothing in the plan would expose it.

Conditions that cite the defect's code are highlighted as a hint, not a verdict. Progress is
kept in the browser; "Download grades" saves a JSON file for `apply`, which can be run after
each sitting. A plan with no conditions is graded missed automatically. Grades are kept in
`<variant>/coverage.json`; a plan is scored once all its defects are graded.

## Reviewer edit distance

```bash
python -m evals.compare plan.md edited.md
```

Edit a generated Markdown plan into what you would actually use, then compare: lines kept,
changed, deleted and added, an edit distance from 0 to 1 (for the test conditions and for the
whole plan), and which conditions were kept, reworded, dropped or added.
