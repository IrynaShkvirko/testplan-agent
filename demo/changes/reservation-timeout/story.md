# Expire stock reservations

Held stock is never released when a customer abandons checkout. Give reservations a lifetime.

## Acceptance criteria

- AC-1: A reservation that is not confirmed within 15 minutes is released and the stock becomes available again.
- AC-2: Confirming a reservation before it expires keeps the stock reserved.
- AC-3: Two customers reserving the last item at the same time must not both succeed.
- AC-4: Expired reservations are cleaned up as needed without slowing checkout.

## Non-goals

- Notifying customers that a reservation expired
