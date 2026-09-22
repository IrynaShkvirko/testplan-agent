# Login lockout

Stop password guessing on customer accounts.

## Acceptance criteria

- AC-1: After 5 failed logins in a row, the account is locked for 15 minutes.
- AC-2: A successful login resets the count of failed logins.
- AC-3: A locked account can log in again once the 15 minutes have passed.
