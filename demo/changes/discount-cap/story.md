# Cap discounts on bundles

Bundles of three or more items were getting very large coupon discounts. Cap them.

## Acceptance criteria

- AC-1: A cart with 3 or more items gets at most 50% off, however large the coupon is.
- AC-2: A cart with fewer than 3 items keeps the full coupon discount.
- AC-3: When the cap reduces a coupon, the customer is shown a reasonable message.
- AC-4: Discounts are rounded to the nearest cent.

## Out of scope

- Loyalty points
- Changing which coupons exist
