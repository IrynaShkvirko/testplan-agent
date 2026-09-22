# Order listing

Support needs to browse orders.

## Acceptance criteria

- AC-1: GET /orders returns orders in id order, 20 per page by default.
- AC-2: The page size can be chosen, up to at most 50.
- AC-3: Page numbers start at 1; page 0 or below is rejected with status 400.
