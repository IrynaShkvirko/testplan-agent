# Cancel an order

Customers ask support to cancel orders they placed by mistake. Let them do it themselves.

## Acceptance criteria

- AC-1: A customer can cancel their own order within 30 minutes of placing it and gets the paid amount back.
- AC-2: After that, cancelling is refused with status 409.
- AC-3: Customers cannot cancel other customers' orders; they get 403.
- AC-4: Cancelling an order that is already cancelled does not refund it again.
