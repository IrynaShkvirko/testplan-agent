# testplan-agent

Turns a code change and its requirements into a **risk-based test plan in which every claim points
at evidence**. Given a unified diff, a story or ticket, and (optionally) a checkout of the
repository, it collects facts, scores risk in code, has a planner draft the plan, and checks the
draft against those facts before anyone reads it.

> **Status: v0.1, offline core.** The collectors, risk model, validators, repair loop and
> renderers are built and tested. The planner in this release is a **rule-based baseline**; there
> is no language-model client yet (planned for v0.2). The prompts are written and tested for
> structure, but **have not been run against a live model**. See [Limits](#limits).

## Why it is built this way

A model asked "write a test plan for this PR" produces something fluent and hard to trust: cases
for code that is not in the diff, invented test names, priorities that do not follow from
anything. This project treats the model as one stage in a pipeline that can be checked:

1. **Collectors** (plain Python, deterministic) read the diff, the story, the repository's tests,
   its import graph, its git history and, if you supply it, coverage. Each finding becomes a
   numbered **fact** (`F12: shop/pricing.py: 3 bug-fix commits in 365 days`).
2. **Risk is computed from those facts by code**: likelihood from size, new code, churn, earlier
   bug fixes, breadth, contract changes and untested changes; impact from the sensitive areas
   touched (money, auth, personal data, data loss, concurrency, time), endpoints and blast
   radius. A planner may move a score by one step, and only with a written reason.
3. **The planner** drafts the plan as JSON that must match a schema. Every case, risk and
   question cites fact ids (`F12`) or `file:line` references.
4. **Validators** check the draft against the facts. A citation that does not resolve, a risk
   that was quietly re-scored, an acceptance criterion with no test, a "covered by" pointing at a
   test that does not exist: each is an error. Errors go back to the planner as a repair prompt,
   at most twice; the attempt with the fewest errors is kept.
5. **A plan that still fails is shipped with its errors shown**, not hidden or silently patched.

## Quick start

Python 3.9 or newer, no runtime dependencies.

```bash
git clone <this repository> && cd testplan-agent
python -m pip install -e ".[dev]"

python demo/run_demo.py                       # three synthetic changes -> demo/plans/
python -m pytest                              # 200+ offline tests
```

On your own change:

```bash
git diff main...my-branch > change.patch

testplan generate \
  --diff change.patch \
  --spec story.md \
  --repo . \
  -o plan.md --json plan.json
```

`--spec` is any Markdown: a story, an issue, a PR description. Criteria are read from an
"Acceptance criteria" style heading, `AC-1:` labels, or Given/When/Then blocks.

## What goes in

| Input | Flag | Used for |
|---|---|---|
| Unified diff | `--diff` (or `-` for stdin) | changed files, changed symbols, signature changes, endpoints, sensitive areas |
| Story, issue or PR text | `--spec` | acceptance criteria, non-goals, vague wording |
| Repository checkout | `--repo` | existing tests that exercise the changed code, dependents, git history, fixtures |
| Coverage report | `--coverage` | changed lines that no test executes (Cobertura XML) |
| Flaky-test list | `--quarantine` (default `quarantine.json` in the repo) | quarantined tests are not counted as coverage; reads the format written by flaky-quarantine |
| Constraints | `--levels`, `--budget` | test levels in scope, time available |

Secrets and email addresses are redacted from the diff and story **before** anything is bundled,
saved or shown to a model (`[REDACTED:kind]`, line-preserving so hunk headers stay valid).

## What comes out

A Markdown plan for people and a JSON plan for tools ([schema](schema/testplan.schema.json)):

1. Summary and scope: change class, criteria in scope, non-goals
2. Risk assessment: a scored table, and a line per risk showing which facts produced which points
3. Test conditions: priority, technique, level, automation, existing coverage, confidence, evidence;
   steps and expected results for P0 and P1
4. Regression scope: existing tests worth re-running, and why
5. Coverage gaps
6. Open questions and assumptions: every vague term and every unmatched requirement or change
7. Environment and data needs
8. Exit criteria
9. Appendix: validator results, every cited fact, run details (generator, context hash)

A condensed excerpt from [`demo/plans/discount-cap.md`](demo/plans/discount-cap.md) (synthetic input, rule-based baseline):

```text
| Risk | What could go wrong                                    | L | I | Score | Level  | Evidence |
| R1   | Money and pricing: wrong behaviour in shop/pricing.py  | 4 | 5 | 20    | high   | F4       |
| R2   | Contract break for callers or API clients              | 4 | 4 | 16    | high   | F2       |
| R3   | Requirements misread: vague or unmatched wording       | 3 | 3 | 9     | medium | F11, F12 |

R1. Likelihood: bug_history +2 (F17 - 3 earlier fixes); contract_change +1 (F2 - signature
    changed or code removed). Impact: sensitive_area +5 (F4 - Money and pricing).

Q1  ambiguity  AC-1: what does 'large' mean in practice? Give a number, a rule or an example
                so it can be tested.                                                    F11
```

## Commands

```text
testplan context   --diff D [--spec S] [--repo R] ...   collect facts, write the context bundle (JSON)
testplan generate  --diff D [--spec S] [--repo R] ...   collect, plan, validate, render
    --show-context        print exactly what would be sent to a model, then stop
    --context FILE        use a saved bundle instead of collecting
    --client heuristic    the rule-based baseline (default)
    --client scripted --responses FILE   canned answers; used by the tests
    --max-repairs N       repair rounds after the first attempt (default 2; the rule-based
                          baseline is deterministic, so it is never asked to repair)
testplan validate  PLAN.json --context BUNDLE.json       check a plan you edited or got elsewhere
testplan schema                                          print the plan JSON Schema
```

Exit codes: `0` plan is clean, `1` a plan was produced but has unresolved errors (it is still
written, with the errors shown), `2` usage error or no usable plan.

## The checks

| Code | Severity | Meaning |
|---|---|---|
| `schema` | error | wrong shape, type or value outside the allowed set |
| `duplicate_id` | error | an id used twice |
| `unknown_fact`, `bad_file_ref` | error | a citation that does not resolve to a fact, or to a real line of a changed or existing file |
| `unknown_risk`, `risk_mismatch`, `adjustment_without_reason`, `risk_dropped` | error | risks must come from the computed list, match its scores unless adjusted by one step with a reason, and no high risk may be omitted |
| `missing_requirement_reason`, `unknown_requirement`, `uncovered_requirement` | error | a case must trace to a criterion or say why it does not, and every criterion needs a case |
| `unknown_test` | error | "existing coverage" or regression items must be tests the scan actually found |
| `missing_steps` | error | P0 and P1 cases need steps and an expected result |
| `priority_mismatch`, `quarantined_coverage`, `missing_confidence_reason`, `too_many_cases`, `unraised_flag`, `class_mismatch`, `risk_without_cases` | warning | worth a reviewer's attention, not blocking |

`tests/test_validate.py` has a failing case for every code and a test that fails if a new code is
added without one.

## Prompt injection

Diffs and tickets are untrusted text. The model gets no tools and its only output is a JSON
object that is validated; the bundle sits inside `<untrusted-context>` tags, `</` and the opening
tag inside content are replaced by JSON escapes so content cannot close the block early, and the
system prompt says to treat everything inside as data. This limits what injected text can do; it
does not make a model immune to being nudged, which is why the validators exist.
`tests/test_prompts.py` covers the delimiter handling.

## Limits

- **No model has been run yet.** The baseline planner fills templates from facts. Its plans are
  well-formed and grounded but generic (for example "Arrange the state that AC-1 describes"); it
  cannot invent test data or judge subtle requirements. The prompts and the repair loop are
  tested against scripted answers only.
- **Requirement-to-code mapping is word overlap**, so it produces questions for a person, never
  verdicts. Existing tests are matched statically (imports, names called, file name) for Python
  only; dynamic dispatch and fixtures that hide the call are missed.
- **Risk weights are a judgement**, written down in `risk.py`. Likelihood is mostly computed for
  the change as a whole, so it is the impact that separates one risk from another. They have not been calibrated against
  real defect data.
- Python source only for symbol-level analysis (other files are classified and counted). Coverage
  input is Cobertura XML. History uses `git log`, with an as-of date so results are reproducible.
- If the checkout contains a line that was redacted from the diff, the file cannot be rebuilt
  from the working tree and symbol lists are marked approximate.
- Tested on Python 3.10 to 3.13; 3.9 is in the CI matrix but has not been run by the author.

## Roadmap

- **v0.2**: an Anthropic client behind the existing `LLMClient` interface, prompt tuning against
  real answers, cost and latency recorded in the plan's run details.
- **v0.3**: a small labelled evaluation set (changes with known defects) to measure whether a
  model's plan beats this baseline on coverage of known failure modes, citation accuracy and
  reviewer edit distance.
- **v0.4**: pull request input, posting the plan as a PR comment, pytest skeleton generation,
  a skill wrapper.

## Development

```bash
python -m pytest            # offline; git is needed for the history and demo tests
ruff check . && ruff format --check .
python demo/run_demo.py     # after changing the collectors or the baseline: refresh demo/plans
```

Layout: `src/testplan_agent/` (`diffparse`, `requirements`, `surface`, `testscan`, `importgraph`,
`gitsignals`, `coverage`, `redact` collect; `bundle`, `risk`, `facts` assemble; `prompts`, `llm`,
`baseline`, `planner`, `validate`, `schema`, `render`, `cli` plan and check), `tests/`, `demo/`,
`schema/`.

## License

MIT
