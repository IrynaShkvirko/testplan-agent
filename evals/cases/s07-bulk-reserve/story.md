# Reserve a whole order

Checkout reserves stock line by line, so a failed line leaves the others reserved.

## Acceptance criteria

- AC-1: An order is reserved in one step: checkout never holds stock for an order it could not reserve.
- AC-2: When a line is out of stock, the error names that SKU.
