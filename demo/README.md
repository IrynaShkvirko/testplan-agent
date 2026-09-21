# Demo

Everything in this folder is **synthetic**. The "shop" is a made-up program of about a hundred
lines, the three changes are made-up, and the git history is generated with fixed dates so the
output is the same on every machine.

```bash
python demo/run_demo.py
```

builds `demo/_repo` (a git repository with ten dated commits), writes the three changes to
`demo/changes/<name>/` (`change.patch` and `story.md`), and writes a plan for each to
`demo/plans/<name>.md` and `.json`.

| Change | What it exercises |
|---|---|
| `discount-cap` | a signature change, vague wording in two criteria ("large", "reasonable"), files with earlier bug-fix commits |
| `reservation-timeout` | a schema migration that deletes rows, concurrency and time criteria, a class rewrite |
| `refund-endpoint` | a new write endpoint, authorisation and idempotency criteria, and a fake credential in a comment to show redaction |

The plans were written by the **rule-based baseline**, not by a language model. They show the
output format and what the validators check. They are also the bar a model-written plan has to
clear, not evidence that a model does well: no model was involved here.

`tests/test_demo.py` regenerates the plans and fails if the committed ones are out of date.
