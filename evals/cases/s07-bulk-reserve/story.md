# Reserve a whole order

Checkout reserves stock line by line, so a failed line leaves the others reserved.

## Acceptance criteria

- AC-1: All lines of an order are reserved, or none are.
- AC-2: When a line is out of stock, the error names that SKU.
