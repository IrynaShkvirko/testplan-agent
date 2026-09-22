# Orders export

Finance wants a CSV of orders for their spreadsheet.

## Acceptance criteria

- AC-1: One row per order with id, amount paid in euros and item names.
- AC-2: Amounts have exactly two decimals, e.g. 12.50 rather than 12.5.
- AC-3: Item names may contain commas or semicolons and must come through unchanged.
