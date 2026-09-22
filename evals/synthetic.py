"""Synthetic evaluation cases: changes to the demo shop with deliberately seeded defects.

    python -m evals.synthetic        # (re)writes evals/cases/s??-*/

Each spec is a change (edits to existing demo files, new files), the story that asked for it,
and the defects planted in it. A defect names a line by a snippet of its text; the generator
resolves it to ``file:line`` in the changed file, so labels cannot drift from the code.

Some defects break a stated acceptance criterion; others are edge cases the story never
mentions, which is where a test planner earns its keep. The stories do not hint at them.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = Path(__file__).resolve().parent / "cases"
AS_OF = "2026-09-01"
DRAFTED_BY = "claude-opus-5 (draft)"

sys.path.insert(0, str(ROOT / "demo"))
import build_demo_repo as demo  # noqa: E402

# Anchors in the final demo files, for appending new code in the right place.
PRICING_END = "    return apply_discount(subtotal, coupon_percent)\n"
INVENTORY_END = "            self._reserved[sku] = max(0, self._reserved.get(sku, 0) - qty)\n"
API_END = '    return {"status": 201, "id": order_id, "total": total}\n'


def defect(id: str, summary: str, at: str, trigger: str, caught_if: str) -> Dict[str, str]:
    path, snippet = at.split("::", 1)
    return {"id": id, "summary": summary, "at": (path, snippet), "trigger": trigger,
            "caught_if": caught_if}  # fmt: skip


SPECS: List[Dict[str, Any]] = [
    {
        "id": "s01-loyalty-points",
        "title": "Loyalty points on paid orders",
        "tags": ["synthetic", "money"],
        "story": """# Loyalty points

Customers should earn points on what they pay and spend them later as a discount.

## Acceptance criteria

- AC-1: A customer earns one point per whole euro of the amount actually paid, after coupons.
- AC-2: 100 points are worth 1 euro off (1 cent per point).
- AC-3: A customer cannot redeem more points than they have.
- AC-4: Redeeming zero or a negative number of points is rejected.
""",
        "edits": {},
        "new": {
            "shop/loyalty.py": '''"""Loyalty points: earned on paid orders, redeemed as a discount."""

POINTS_PER_EURO = 1
CENTS_PER_POINT = 1


class NotEnoughPoints(Exception):
    pass


class LoyaltyAccount:
    def __init__(self):
        self.balance = 0

    def earn(self, items, coupon_percent=0):
        """Add points for an order: one point per whole euro paid."""
        subtotal = sum(price * qty for _, price, qty in items)
        points = subtotal // 100 * POINTS_PER_EURO
        self.balance += points
        return points

    def redeem(self, points):
        """Spend points; returns the discount in cents."""
        if points > self.balance:
            raise NotEnoughPoints(points)
        self.balance -= points
        return points * CENTS_PER_POINT
'''
        },
        "defects": [
            defect(
                "D1",
                "Points are earned on the subtotal before the coupon, not on the amount paid.",
                "shop/loyalty.py::subtotal = sum(price * qty for _, price, qty in items)",
                "an order with a coupon, e.g. 20.00 euros with 50% off",
                "a condition earns points on an order with a coupon and checks they follow the "
                "discounted total (10 points, not 20)",
            ),
            defect(
                "D2",
                "Redeeming zero or negative points is accepted; a negative redemption raises "
                "the balance.",
                "shop/loyalty.py::if points > self.balance:",
                "redeem(0) or redeem(-500)",
                "a condition redeems zero or a negative number of points and checks it is "
                "rejected with the balance unchanged",
            ),
        ],
    },
    {
        "id": "s02-free-shipping",
        "title": "Free shipping from 50 euros",
        "tags": ["synthetic", "money"],
        "story": """# Free shipping threshold

Shipping is a flat 4.99 euros. Make larger orders ship free.

## Acceptance criteria

- AC-1: Orders of 50.00 euros or more ship free.
- AC-2: The threshold applies to the total after the coupon.
- AC-3: Other orders pay 4.99 euros shipping, included in the order total.
""",
        "edits": {
            "shop/pricing.py": [
                (
                    PRICING_END,
                    PRICING_END
                    + '''

SHIPPING_CENTS = 499
FREE_SHIPPING_FROM_CENTS = 5000


def shipping_cost(items, coupon_percent=0):
    """Shipping in cents: free from 50 euros, after discounts."""
    subtotal = sum(price * qty for _, price, qty in items)
    if subtotal > FREE_SHIPPING_FROM_CENTS:
        return 0
    return SHIPPING_CENTS
''',
                )
            ],
            "shop/api.py": [
                (
                    '    total = pricing.cart_total(items, request.get("coupon", 0))\n',
                    '    coupon = request.get("coupon", 0)\n'
                    "    total = pricing.cart_total(items, coupon) + pricing.shipping_cost(items, coupon)\n",
                )
            ],
        },
        "new": {},
        "defects": [
            defect(
                "D1",
                "An order of exactly 50.00 euros pays shipping: the comparison is > instead of >=.",
                "shop/pricing.py::if subtotal > FREE_SHIPPING_FROM_CENTS:",
                "a cart totalling exactly 5000 cents",
                "a condition checks shipping at exactly 50.00 euros (and just below) and "
                "expects it free at 50.00",
            ),
            defect(
                "D2",
                "The threshold is checked on the subtotal before the coupon.",
                "shop/pricing.py::    subtotal = sum(price * qty for _, price, qty in items)\n    if subtotal >",
                "a cart of 60 euros with a 25% coupon (45 euros paid)",
                "a condition uses a coupon that takes the total below 50 euros and checks "
                "shipping is charged",
            ),
        ],
    },
    {
        "id": "s03-restock",
        "title": "Restock deliveries",
        "tags": ["synthetic", "concurrency"],
        "story": """# Restock deliveries

The warehouse needs to add delivered stock through the inventory module.

## Acceptance criteria

- AC-1: Restocking adds the delivered quantity to the SKU's stock and returns the new stock.
- AC-2: A SKU that has never been stocked can be restocked.
- AC-3: Several warehouse workers record deliveries at the same time, while customers reserve.
- AC-4: A delivery must be a positive quantity.
""",
        "edits": {
            "shop/inventory.py": [
                (
                    INVENTORY_END,
                    INVENTORY_END
                    + '''
    def restock(self, sku, qty):
        """Add delivered stock for a SKU."""
        if qty == 0:
            return self._stock.get(sku, 0)
        self._stock[sku] = self._stock.get(sku, 0) + qty
        return self._stock[sku]
''',
                )
            ]
        },
        "new": {},
        "defects": [
            defect(
                "D1",
                "Restock updates the stock without the inventory lock, so concurrent restocks "
                "or reservations can lose updates.",
                "shop/inventory.py::self._stock[sku] = self._stock.get(sku, 0) + qty",
                "two workers restocking the same SKU at once, or a restock during a reservation",
                "a condition restocks the same SKU from several threads (or alongside reserve) "
                "and checks the final stock is the sum of all deliveries",
            ),
            defect(
                "D2",
                "A negative quantity is accepted and reduces stock; only zero is special-cased.",
                "shop/inventory.py::if qty == 0:",
                "restock('pen', -5)",
                "a condition restocks a negative quantity and checks it is rejected with stock "
                "unchanged",
            ),
        ],
    },
    {
        "id": "s04-order-cancel",
        "title": "Customers cancel recent orders",
        "tags": ["synthetic", "auth"],
        "story": """# Cancel an order

Customers ask support to cancel orders they placed by mistake. Let them do it themselves.

## Acceptance criteria

- AC-1: A customer can cancel their own order within 30 minutes of placing it and gets the paid amount back.
- AC-2: After that, cancelling is refused with status 409.
- AC-3: Customers cannot cancel other customers' orders; they get 403.
- AC-4: Cancelling an order that is already cancelled does not refund it again.
""",
        "edits": {
            "shop/api.py": [
                (
                    '"""HTTP handlers."""\n',
                    '"""HTTP handlers."""\nfrom datetime import datetime, timedelta\n\n',
                ),
                (
                    '    ORDERS[order_id] = {"items": items, "paid_cents": total}\n',
                    "    ORDERS[order_id] = {\n"
                    '        "items": items,\n'
                    '        "paid_cents": total,\n'
                    '        "customer": request.get("user", {}).get("id"),\n'
                    '        "created_at": request.get("now") or datetime.utcnow(),\n'
                    '        "status": "placed",\n'
                    "    }\n",
                ),
                (
                    API_END,
                    API_END
                    + """

CANCEL_WINDOW = timedelta(minutes=30)


@app.post("/orders/cancel")
def cancel_order(request):
    order = ORDERS.get(request["order_id"])
    if order is None:
        return {"status": 404}
    now = request.get("now") or datetime.utcnow()
    if now - order["created_at"] > CANCEL_WINDOW:
        return {"status": 409}
    order["status"] = "cancelled"
    return {"status": 200, "refund_cents": order["paid_cents"]}
""",
                ),
            ]
        },
        "new": {},
        "defects": [
            defect(
                "D1",
                "Any caller can cancel any order: the order's customer is never compared with "
                "the requesting user.",
                'shop/api.py::order = ORDERS.get(request["order_id"])',
                "customer B cancels customer A's order",
                "a condition cancels an order as a different customer and expects 403 with the "
                "order unchanged",
            ),
            defect(
                "D2",
                "Cancelling an already cancelled order returns the refund again.",
                'shop/api.py::order["status"] = "cancelled"',
                "the same cancel request sent twice",
                "a condition cancels the same order twice and checks the second call does not "
                "refund again",
            ),
        ],
    },
    {
        "id": "s05-price-format",
        "title": "Format prices for receipts",
        "tags": ["synthetic", "money"],
        "story": """# Price formatting

Receipts print raw cents. Show euros instead.

## Acceptance criteria

- AC-1: Amounts are shown in euros with exactly two decimals, e.g. 1234 cents is "€12.34".
- AC-2: Refunds are negative amounts and are shown with a leading minus, e.g. "-€1.50".
""",
        "edits": {},
        "new": {
            "shop/money.py": '''"""Money formatting for receipts."""


def format_eur(cents):
    """Format cents as euros, e.g. 1234 -> '€12.34'."""
    euros, rest = divmod(cents, 100)
    return f"€{euros}.{rest}"
'''
        },
        "defects": [
            defect(
                "D1",
                "Cents below 10 lose their leading zero: 105 cents prints as €1.5.",
                'shop/money.py::return f"€{euros}.{rest}"',
                "an amount like 105 or 1000 cents",
                "a condition formats an amount whose cents part is below 10 and checks two "
                "decimals are shown",
            ),
            defect(
                "D2",
                "Negative amounts are formatted wrongly: divmod floors, so -150 prints as €-2.50.",
                "shop/money.py::euros, rest = divmod(cents, 100)",
                "a refund such as -150 cents",
                "a condition formats a negative amount and expects -€1.50",
            ),
        ],
    },
    {
        "id": "s06-coupon-codes",
        "title": "Coupon codes with expiry dates",
        "tags": ["synthetic", "time"],
        "story": """# Coupon codes

Marketing wants named coupon codes that stop working after a date.

## Acceptance criteria

- AC-1: Codes are case-insensitive: "spring10" works like "SPRING10".
- AC-2: A coupon is valid up to and including its last day.
- AC-3: Unknown or expired codes are rejected with an error.
""",
        "edits": {},
        "new": {
            "shop/coupons.py": '''"""Named coupon codes."""
from datetime import date

COUPONS = {
    "SPRING10": (10, date(2026, 5, 31)),
    "WELCOME20": (20, date(2026, 12, 31)),
}


class InvalidCoupon(Exception):
    pass


def coupon_percent(code, today):
    """Percent off for a coupon code that has not expired."""
    if code not in COUPONS:
        raise InvalidCoupon(code)
    percent, last_day = COUPONS[code]
    if today >= last_day:
        raise InvalidCoupon(code)
    return percent
'''
        },
        "defects": [
            defect(
                "D1",
                "Lookup is case-sensitive: 'spring10' is rejected.",
                "shop/coupons.py::if code not in COUPONS:",
                "a lower- or mixed-case code",
                "a condition uses a code in another case and expects it accepted",
            ),
            defect(
                "D2",
                "A coupon is rejected on its last day: the comparison is >= instead of >.",
                "shop/coupons.py::if today >= last_day:",
                "using SPRING10 on 2026-05-31",
                "a condition uses a coupon on its last day (and the day after) and expects it "
                "valid on the last day",
            ),
        ],
    },
    {
        "id": "s07-bulk-reserve",
        "title": "Reserve every line of an order at once",
        "tags": ["synthetic", "concurrency"],
        "story": """# Reserve a whole order

Checkout reserves stock line by line, so a failed line leaves the others reserved.

## Acceptance criteria

- AC-1: All lines of an order are reserved, or none are.
- AC-2: When a line is out of stock, the error names that SKU.
""",
        "edits": {
            "shop/inventory.py": [
                (
                    INVENTORY_END,
                    INVENTORY_END
                    + '''
    def reserve_many(self, lines):
        """Reserve several SKUs for one order: all of them, or none."""
        with self._lock:
            for sku, qty in lines:
                if self.available(sku) < qty:
                    raise OutOfStock(sku)
                self._reserved[sku] = self._reserved.get(sku, 0) + qty
''',
                )
            ]
        },
        "new": {},
        "defects": [
            defect(
                "D1",
                "Lines reserved before an out-of-stock line stay reserved: nothing is rolled back.",
                "shop/inventory.py::                self._reserved[sku] = self._reserved.get(sku, 0) + qty",
                "an order whose second line is out of stock",
                "a condition reserves an order where a later line fails and checks the earlier "
                "lines' stock is still available",
            ),
        ],
    },
    {
        "id": "s08-order-listing",
        "title": "List orders page by page",
        "tags": ["synthetic", "endpoint"],
        "story": """# Order listing

Support needs to browse orders.

## Acceptance criteria

- AC-1: GET /orders returns orders in id order, 20 per page by default.
- AC-2: The page size can be chosen, up to at most 50.
- AC-3: Page numbers start at 1; page 0 or below is rejected with status 400.
""",
        "edits": {
            "shop/api.py": [
                (
                    API_END,
                    API_END
                    + """

PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


@app.get("/orders")
def list_orders(request):
    page = int(request.get("page", 1))
    size = int(request.get("size", PAGE_SIZE))
    ids = sorted(ORDERS)
    start = (page - 1) * size
    chunk = ids[start + 1 : start + size]
    return {"status": 200, "page": page, "orders": [{"id": i, **ORDERS[i]} for i in chunk]}
""",
                )
            ]
        },
        "new": {},
        "defects": [
            defect(
                "D1",
                "Each page skips its first order and returns one order too few.",
                "shop/api.py::chunk = ids[start + 1 : start + size]",
                "listing page 1 of three orders",
                "a condition lists a known set of orders and checks the first page starts with "
                "the first order and holds the full page size",
            ),
            defect(
                "D2",
                "The page size is never capped at 50.",
                'shop/api.py::size = int(request.get("size", PAGE_SIZE))',
                "size=500",
                "a condition asks for more than 50 per page and checks at most 50 come back "
                "(or the request is refused)",
            ),
            defect(
                "D3",
                "Page 0 or a negative page is not rejected; it returns a slice from the end.",
                'shop/api.py::page = int(request.get("page", 1))',
                "page=0 or page=-1",
                "a condition asks for page 0 and expects status 400",
            ),
        ],
    },
    {
        "id": "s09-vat",
        "title": "VAT on orders",
        "tags": ["synthetic", "money"],
        "story": """# VAT

Invoices must show VAT. Books have a reduced rate.

## Acceptance criteria

- AC-1: VAT is 20% for standard items and 5% for books.
- AC-2: VAT is computed per rate on the order total and rounded once, to the nearest cent.
- AC-3: Items without a category use the standard rate.
""",
        "edits": {},
        "new": {
            "shop/tax.py": '''"""VAT for invoices."""

VAT_RATES = {"standard": 20, "books": 5}


def order_vat(items):
    """VAT in cents for (name, price_cents, qty, category) items."""
    vat = 0
    for _, price, qty, category in items:
        vat += round(price * qty * VAT_RATES[category] / 100)
    return vat
'''
        },
        "defects": [
            defect(
                "D1",
                "VAT is rounded per line instead of once on the total, so small lines lose cents.",
                "shop/tax.py::vat += round(price * qty * VAT_RATES[category] / 100)",
                "three lines of 1.02 euros at 20%: each line's 20.4 cents rounds to 20, so 60 "
                "in all, while 61.2 cents on the total rounds to 61",
                "a condition uses several small lines whose per-line VAT rounds differently "
                "from the total, and checks VAT is rounded once",
            ),
            defect(
                "D2",
                "An item without a category crashes instead of using the standard rate.",
                "shop/tax.py::for _, price, qty, category in items:",
                "an item given as (name, price, qty) or with category None",
                "a condition includes an item without a category and expects 20% VAT on it",
            ),
        ],
    },
    {
        "id": "s10-order-status-migration",
        "title": "Track order status",
        "tags": ["synthetic", "migration"],
        "story": """# Order status

Orders need a status so support can see what happened to them.

## Acceptance criteria

- AC-1: Every order has a status: placed, shipped or cancelled.
- AC-2: New orders start as placed.
- AC-3: Existing orders are marked as placed when the change is deployed.
""",
        "edits": {
            "shop/api.py": [
                (
                    '    ORDERS[order_id] = {"items": items, "paid_cents": total}\n',
                    '    ORDERS[order_id] = {"items": items, "paid_cents": total, "status": "placed"}\n',
                )
            ]
        },
        "new": {
            "migrations/0004_add_order_status.sql": (
                "ALTER TABLE orders ADD COLUMN status TEXT NOT NULL;\n"
                "UPDATE orders SET status = 'paid';\n"
            )
        },
        "defects": [
            defect(
                "D1",
                "Adding a NOT NULL column without a default fails on a table that already has rows.",
                "migrations/0004_add_order_status.sql::ALTER TABLE orders ADD COLUMN status TEXT NOT NULL;",
                "running the migration against a database with existing orders",
                "a condition applies the migration to a populated orders table and checks it "
                "succeeds",
            ),
            defect(
                "D2",
                "Existing orders are backfilled as 'paid', a status that does not exist; they "
                "should be 'placed'.",
                "migrations/0004_add_order_status.sql::UPDATE orders SET status = 'paid';",
                "any order that existed before the migration",
                "a condition migrates existing orders and checks their status is placed",
            ),
        ],
    },
    {
        "id": "s11-login-lockout",
        "title": "Lock accounts after failed logins",
        "tags": ["synthetic", "auth"],
        "story": """# Login lockout

Stop password guessing on customer accounts.

## Acceptance criteria

- AC-1: After 5 failed logins in a row, the account is locked for 15 minutes.
- AC-2: A successful login resets the count of failed logins.
- AC-3: A locked account can log in again once the 15 minutes have passed.
""",
        "edits": {},
        "new": {
            "shop/auth.py": '''"""Login lockout after repeated failures."""
from datetime import timedelta

MAX_FAILURES = 5
LOCKOUT = timedelta(minutes=15)


class LockedOut(Exception):
    pass


class LoginGuard:
    def __init__(self):
        self._failures = {}
        self._locked_until = {}

    def check(self, user, now):
        until = self._locked_until.get(user)
        if until and now < until:
            raise LockedOut(user)

    def record_failure(self, user, now):
        count = self._failures.get(user, 0) + 1
        self._failures[user] = count
        if count > MAX_FAILURES:
            self._locked_until[user] = now + LOCKOUT

    def record_success(self, user):
        self._locked_until.pop(user, None)
'''
        },
        "defects": [
            defect(
                "D1",
                "The account locks only after the 6th failure: the comparison is > instead of >=.",
                "shop/auth.py::if count > MAX_FAILURES:",
                "exactly 5 failed logins",
                "a condition fails 5 times and checks the account is locked (and not after 4)",
            ),
            defect(
                "D2",
                "A successful login does not reset the failure count.",
                "shop/auth.py::self._locked_until.pop(user, None)",
                "4 failures, a success, then 2 failures",
                "a condition fails, succeeds, then fails again and checks the count started "
                "over (no lock after 2 new failures)",
            ),
        ],
    },
    {
        "id": "s12-orders-csv",
        "title": "Export orders as CSV",
        "tags": ["synthetic", "data"],
        "story": """# Orders export

Finance wants a CSV of orders for their spreadsheet.

## Acceptance criteria

- AC-1: One row per order with id, amount paid in euros and item names.
- AC-2: Amounts have exactly two decimals, e.g. 12.50 rather than 12.5.
- AC-3: Item names may contain commas or semicolons and must come through unchanged.
""",
        "edits": {},
        "new": {
            "shop/export.py": '''"""CSV export of orders."""
import csv
import io


def orders_csv(orders):
    """CSV of orders: id, paid in euros, item names."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "paid_eur", "items"])
    for order_id, order in orders.items():
        items = ";".join(name for name, _, _ in order["items"])
        writer.writerow([order_id, order["paid_cents"] / 100, items])
    return out.getvalue()
'''
        },
        "defects": [
            defect(
                "D1",
                "Amounts lose trailing zeros: 1250 cents is written as 12.5.",
                'shop/export.py::writer.writerow([order_id, order["paid_cents"] / 100, items])',
                "an order paid 1250 or 1000 cents",
                "a condition exports an amount ending in zero cents and checks two decimals",
            ),
            defect(
                "D2",
                "Item names are joined with ';', so a name containing ';' cannot be told apart "
                "from two items.",
                'shop/export.py::items = ";".join(name for name, _, _ in order["items"])',
                "an item named 'pens; blue'",
                "a condition exports an item name containing a semicolon and checks it is read "
                "back as one item",
            ),
        ],
    },
]


def _line_of(text: str, snippet: str, where: str) -> int:
    index = text.find(snippet)
    if index < 0 or text.find(snippet, index + 1) >= 0:
        raise ValueError(f"{where}: snippet must occur exactly once: {snippet!r}")
    return text.count("\n", 0, index) + 1


# What running each trigger against the generated code showed (see evals/README.md).
OBSERVED = {
    (
        "s01-loyalty-points",
        "D1",
    ): "earn([('x', 2000, 1)], coupon_percent=50) returned 20; 10 expected",
    ("s01-loyalty-points", "D2"): "balance 100, redeem(-500): accepted, balance became 600",
    ("s02-free-shipping", "D1"): "shipping_cost for exactly 5000 cents returned 499; 0 expected",
    ("s02-free-shipping", "D2"): "6000 cents with a 25% coupon: shipping 0; 499 expected",
    ("s03-restock", "D1"): "restock() never takes self._lock (checked in the source)",
    ("s03-restock", "D2"): "stock 10, restock('pen', -5) returned 5 instead of rejecting",
    ("s04-order-cancel", "D1"): "customer B cancelled customer A's order: status 200 with a refund",
    ("s04-order-cancel", "D2"): "second cancel of the same order: status 200 and a second refund",
    ("s05-price-format", "D1"): "format_eur(105) returned '€1.5'",
    ("s05-price-format", "D2"): "format_eur(-150) returned '€-2.50'",
    ("s06-coupon-codes", "D1"): "coupon_percent('spring10', 2026-05-01) raised InvalidCoupon",
    ("s06-coupon-codes", "D2"): "coupon_percent('SPRING10', 2026-05-31) raised InvalidCoupon",
    (
        "s07-bulk-reserve",
        "D1",
    ): "pen 10, ink 0; reserve_many([pen 3, ink 1]) failed and left pen at 7",
    ("s08-order-listing", "D1"): "3 orders, size=2: page 1 returned ids [2]; [1, 2] expected",
    ("s08-order-listing", "D2"): "60 orders, size=500: 59 returned (none capped at 50)",
    ("s08-order-listing", "D3"): "page=0 returned status 200",
    ("s09-vat", "D1"): "three lines of 102 cents at 20%: order_vat returned 60; 61 expected",
    ("s09-vat", "D2"): "an item (name, price, qty) raised ValueError (not enough values to unpack)",
    (
        "s10-order-status-migration",
        "D1",
    ): "SQLite, orders with one row: 'Cannot add a NOT NULL column with default value NULL'",
    (
        "s10-order-status-migration",
        "D2",
    ): "the backfill writes 'paid'; the story's statuses are placed, shipped, cancelled",
    ("s11-login-lockout", "D1"): "after 5 failures check() did not raise LockedOut",
    ("s11-login-lockout", "D2"): "4 failures, a success, 2 failures: account locked",
    ("s12-orders-csv", "D1"): "an order paid 1250 cents exported as '1,12.5,pen'",
    (
        "s12-orders-csv",
        "D2",
    ): "item 'pens; blue' exported as 'pens; blue', indistinguishable from two items",
}


def _label_key(d: Dict[str, str]) -> tuple:
    return (d["id"], d["summary"], d["location"], d["trigger"], d["caught_if"])


def _verifications(path: Path) -> Dict[tuple, str]:
    """Who verified each label so far. A verification survives only an unchanged label."""
    if not path.is_file():
        return {}
    old = json.loads(path.read_text(encoding="utf-8"))
    return {_label_key(d): d.get("verified_by", "") for d in old.get("defects", [])}


def generate(out: Path = CASES_DIR, keep_from: Optional[Path] = None) -> List[str]:
    """Write every synthetic case folder under ``out``; returns the case ids.

    Verifications are carried over from ``keep_from`` (default: ``out``) for labels whose
    content is unchanged.
    """
    keep_from = keep_from or out
    ids = []
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        demo.build(repo, Path(tmp) / "changes")
        final = demo._final_contents()
        for spec in SPECS:
            after: Dict[str, str] = {}
            for path, edits in spec["edits"].items():
                after[path] = demo._apply(final[path], edits, path)
                (repo / path).write_text(after[path], encoding="utf-8")
            for path, text in spec["new"].items():
                target = repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
                after[path] = text
                demo._git(repo, "add", "-N", path)
            patch = demo._strip_index_lines(
                demo._git(repo, "diff", "--no-color", "--no-ext-diff", "-U3")
            )
            demo._git(repo, "reset", "-q", "--hard")
            demo._git(repo, "clean", "-fdq")

            folder = out / spec["id"]
            kept = _verifications(keep_from / spec["id"] / "case.json")
            defects = []
            for d in spec["defects"]:
                path, snippet = d["at"]
                line = _line_of(after[path], snippet, f"{spec['id']} {d['id']}")
                defects.append(
                    {
                        "id": d["id"],
                        "summary": d["summary"],
                        "location": f"{path}:{line}",
                        "trigger": d["trigger"],
                        "caught_if": d["caught_if"],
                        "written_by": DRAFTED_BY,
                        "verified_by": "",
                        "observed": OBSERVED.get((spec["id"], d["id"]), ""),
                    }
                )
                label = defects[-1]
                label["verified_by"] = kept.get(_label_key(label), "")
            if folder.exists():
                shutil.rmtree(folder)
            folder.mkdir(parents=True)
            (folder / "change.patch").write_text(patch, encoding="utf-8")
            (folder / "story.md").write_text(spec["story"], encoding="utf-8")
            case = {
                "id": spec["id"],
                "title": spec["title"],
                "tags": spec["tags"],
                "repo": {"kind": "demo"},
                "as_of": AS_OF,
                "notes": "Synthetic change to the demo shop with seeded defects.",
                "defects": defects,
            }
            (folder / "case.json").write_text(json.dumps(case, indent=2) + "\n", encoding="utf-8")
            ids.append(spec["id"])
    return ids


if __name__ == "__main__":
    print("wrote", ", ".join(generate()))
