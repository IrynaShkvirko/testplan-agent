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

## Metrics so far

| Metric | Meaning |
|---|---|
| `checks_clean` | the final plan passes every check |
| `first_try_clean` | ...and needed no repair |
| `citations_first` | share of the first answer's citations that resolve, before repairs |

Coverage of known defects, the headline metric, is added with the grading worksheet.
