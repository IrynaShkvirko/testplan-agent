# Evaluation cases

One folder per case, named after its id, holding `case.json`:

```json
{
  "id": "discount-rounding",
  "title": "Round discounted totals to whole cents",
  "tags": ["synthetic", "money"],
  "repo": {"kind": "demo"},
  "as_of": "2026-09-01",
  "notes": "One line on what the change does and why it is here.",
  "defects": [
    {
      "id": "D1",
      "summary": "Totals are truncated, not rounded, so 0.5 cents is lost.",
      "location": "shop/pricing.py:21",
      "trigger": "a discount that leaves half a cent, e.g. 10% off 105 cents",
      "caught_if": "a condition computes a total ending in half a cent and checks it rounds up",
      "written_by": "claude-opus-5",
      "verified_by": ""
    }
  ]
}
```

- The diff and the story sit next to it as `change.patch` and `story.md`, or `"demo_change"`
  names one of the generated demo changes instead.
- `tags[0]` groups cases in the report; further tags show next to each case.
- `defects` is the answer key for coverage. `verified_by` stays empty until a person has
  checked the label against the code; unverified labels are reported as such.
