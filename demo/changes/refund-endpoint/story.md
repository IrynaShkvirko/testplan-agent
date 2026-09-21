# Refund endpoint

Support currently issues refunds by hand in the database. Add an endpoint.

## Acceptance criteria

- AC-1: Support staff can refund up to the paid amount of an order with POST /refunds.
- AC-2: A refund larger than the amount paid is rejected with status 422.
- AC-3: Only users with the support role can issue refunds; everyone else gets 403.
- AC-4: A refund is recorded once even if the request is retried.

## Out of scope

- Partial refunds of shipping costs
