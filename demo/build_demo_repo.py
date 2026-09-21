"""Build the demo repository (git history included) and the three change fixtures.

Everything is synthetic and deterministic: fixed authors, fixed commit dates, fixed contents.

    python demo/build_demo_repo.py                 # writes demo/_repo and demo/changes/*
    python demo/build_demo_repo.py --repo /tmp/x   # somewhere else
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

HERE = Path(__file__).resolve().parent
MARKER = ".testplan-demo"

# --------------------------------------------------------------------------------------------
# Base files. Each history commit appends a changelog line, so `git log` has something to say.
# --------------------------------------------------------------------------------------------
PRICING = '''"""Pricing rules for carts."""

MAX_DISCOUNT_PERCENT = 100


def apply_discount(price_cents, percent):
    """Return price_cents reduced by percent, rounded to the nearest cent."""
    if not 0 <= percent <= MAX_DISCOUNT_PERCENT:
        raise ValueError("percent must be between 0 and 100")
    return round(price_cents * (100 - percent) / 100)


def cart_total(items, coupon_percent=0):
    """Total in cents for a list of (name, price_cents, quantity) items."""
    subtotal = sum(price * qty for _, price, qty in items)
    return apply_discount(subtotal, coupon_percent)
'''

INVENTORY = '''"""Stock and reservations."""
import threading


class OutOfStock(Exception):
    pass


class Inventory:
    def __init__(self, stock):
        self._stock = dict(stock)
        self._reserved = {}
        self._lock = threading.Lock()

    def available(self, sku):
        return self._stock.get(sku, 0) - self._reserved.get(sku, 0)

    def reserve(self, sku, qty):
        with self._lock:
            if self.available(sku) < qty:
                raise OutOfStock(sku)
            self._reserved[sku] = self._reserved.get(sku, 0) + qty

    def release(self, sku, qty):
        with self._lock:
            self._reserved[sku] = max(0, self._reserved.get(sku, 0) - qty)
'''

WEB = '''"""A minimal router so the demo needs no web framework."""


class App:
    def __init__(self):
        self.routes = {}

    def _register(self, method, path):
        def decorator(func):
            self.routes[(method, path)] = func
            return func

        return decorator

    def get(self, path):
        return self._register("GET", path)

    def post(self, path):
        return self._register("POST", path)


app = App()
'''

API = '''"""HTTP handlers."""
from shop import pricing
from shop.web import app

ORDERS = {}


@app.post("/orders")
def create_order(request):
    items = request["items"]
    total = pricing.cart_total(items, request.get("coupon", 0))
    order_id = len(ORDERS) + 1
    ORDERS[order_id] = {"items": items, "paid_cents": total}
    return {"status": 201, "id": order_id, "total": total}
'''

TEST_PRICING = """from shop import pricing


def test_apply_discount_rounds_to_the_nearest_cent():
    assert pricing.apply_discount(999, 33) == 669


def test_apply_discount_rejects_percent_over_100():
    try:
        pricing.apply_discount(100, 101)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_cart_total_applies_coupon():
    assert pricing.cart_total([("pen", 200, 2)], coupon_percent=25) == 300
"""

TEST_INVENTORY = """import pytest

from shop.inventory import Inventory, OutOfStock


def test_reserve_reduces_available_stock(inventory):
    inventory.reserve("pen", 2)
    assert inventory.available("pen") == 8


def test_reserve_more_than_available_fails(inventory):
    with pytest.raises(OutOfStock):
        inventory.reserve("pen", 11)


def test_release_never_goes_negative(inventory):
    inventory.release("pen", 5)
    assert inventory.available("pen") == 10
"""

TEST_API = """from shop import api


def test_create_order_returns_total():
    reply = api.create_order({"items": [("pen", 200, 2)], "coupon": 10})
    assert reply["status"] == 201
    assert reply["total"] == 360
"""

CONFTEST = """import pytest

from shop.inventory import Inventory


@pytest.fixture
def inventory():
    return Inventory({"pen": 10})
"""

PYTEST_INI = "[pytest]\npythonpath = .\n"

MIGRATIONS = {
    "migrations/0001_create_orders.sql": "CREATE TABLE orders (id INTEGER PRIMARY KEY, paid_cents INTEGER NOT NULL);\n",
    "migrations/0002_create_stock.sql": "CREATE TABLE stock (sku TEXT PRIMARY KEY, qty INTEGER NOT NULL);\n",
    "migrations/0003_create_reservations.sql": (
        "CREATE TABLE reservations (id INTEGER PRIMARY KEY, sku TEXT NOT NULL, qty INTEGER NOT NULL, "
        "created_at TIMESTAMP NOT NULL);\n"
    ),
}

# (date, author, subject, {path: content}); files with code get a changelog line appended.
HISTORY: List[Tuple[str, str, str, Dict[str, str]]] = [
    (
        "2026-03-02T10:00:00",
        "Alice",
        "feat: pricing module",
        {"shop/__init__.py": "", "shop/pricing.py": PRICING},
    ),
    (
        "2026-03-10T10:00:00",
        "Alice",
        "feat: inventory reservations",
        {"shop/inventory.py": INVENTORY},
    ),
    ("2026-03-24T10:00:00", "Bob", "feat: orders api", {"shop/web.py": WEB, "shop/api.py": API}),
    (
        "2026-04-15T10:00:00",
        "Bob",
        "fix: discount rounding on 100% coupon",
        {"shop/pricing.py": PRICING},
    ),
    (
        "2026-05-06T10:00:00",
        "Alice",
        "fix: reservation release could go negative",
        {"shop/inventory.py": INVENTORY},
    ),
    (
        "2026-06-02T10:00:00",
        "Bob",
        "test: add pricing, inventory and api tests",
        {
            "tests/test_pricing.py": TEST_PRICING,
            "tests/test_inventory.py": TEST_INVENTORY,
            "tests/test_api.py": TEST_API,
            "tests/conftest.py": CONFTEST,
            "pytest.ini": PYTEST_INI,
        },
    ),
    (
        "2026-06-20T10:00:00",
        "Alice",
        "fix: coupon over 100% was accepted",
        {"shop/pricing.py": PRICING},
    ),
    ("2026-07-15T10:00:00", "Bob", "refactor: split cart_total", {"shop/pricing.py": PRICING}),
    (
        "2026-08-05T10:00:00",
        "Alice",
        "fix: order total ignored coupon",
        {"shop/api.py": API, "shop/pricing.py": PRICING},
    ),
    ("2026-08-20T10:00:00", "Bob", "chore: add migrations", dict(MIGRATIONS)),
]
CODE_SUFFIXES = (".py",)


def _final_contents() -> Dict[str, str]:
    """What each file looks like at the last commit that touches it."""
    final: Dict[str, str] = {}
    for _, _, subject, files in HISTORY:
        for path, text in files.items():
            final[path] = _with_changelog(path, text, subject)
    return final


def _with_changelog(path: str, text: str, subject: str) -> str:
    if path.endswith(CODE_SUFFIXES) and text:
        return text + f"\n# changelog: {subject}\n"
    return text


def _git(repo: Path, *args: str, env: Dict[str, str] = None, check: bool = True) -> str:
    full = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    full.update(env or {})
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=full, check=False
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


# --------------------------------------------------------------------------------------------
# The three changes: edits to existing files (exact replacements) and new files.
# --------------------------------------------------------------------------------------------
Edit = Tuple[str, str]
CHANGES: Dict[str, Dict[str, object]] = {}

CHANGES["discount-cap"] = {
    "title": "Cap discounts on bundles",
    "story": """# Cap discounts on bundles

Bundles of three or more items were getting very large coupon discounts. Cap them.

## Acceptance criteria

- AC-1: A cart with 3 or more items gets at most 50% off, however large the coupon is.
- AC-2: A cart with fewer than 3 items keeps the full coupon discount.
- AC-3: When the cap reduces a coupon, the customer is shown a reasonable message.
- AC-4: Discounts are rounded to the nearest cent.

## Out of scope

- Loyalty points
- Changing which coupons exist
""",
    "edits": {
        "shop/pricing.py": [
            (
                "MAX_DISCOUNT_PERCENT = 100\n",
                "MAX_DISCOUNT_PERCENT = 100\nBUNDLE_MIN_ITEMS = 3\nBUNDLE_MAX_DISCOUNT_PERCENT = 50\n",
            ),
            (
                "def apply_discount(price_cents, percent):\n",
                "def apply_discount(price_cents, percent, item_count=1):\n",
            ),
            (
                '        raise ValueError("percent must be between 0 and 100")\n',
                '        raise ValueError("percent must be between 0 and 100")\n'
                "    if item_count >= BUNDLE_MIN_ITEMS:\n"
                "        percent = min(percent, BUNDLE_MAX_DISCOUNT_PERCENT)\n",
            ),
            (
                "    return apply_discount(subtotal, coupon_percent)\n",
                "    item_count = sum(qty for _, _, qty in items)\n"
                "    return apply_discount(subtotal, coupon_percent, item_count)\n",
            ),
        ]
    },
    "new": {},
}

CHANGES["reservation-timeout"] = {
    "title": "Expire stock reservations after 15 minutes",
    "story": """# Expire stock reservations

Held stock is never released when a customer abandons checkout. Give reservations a lifetime.

## Acceptance criteria

- AC-1: A reservation that is not confirmed within 15 minutes is released and the stock becomes available again.
- AC-2: Confirming a reservation before it expires keeps the stock reserved.
- AC-3: Two customers reserving the last item at the same time must not both succeed.
- AC-4: Expired reservations are cleaned up as needed without slowing checkout.

## Non-goals

- Notifying customers that a reservation expired
""",
    "edits": {
        "shop/inventory.py": [
            (
                '"""Stock and reservations."""\nimport threading\n',
                '"""Stock and reservations with an expiry."""\nimport itertools\nimport threading\n'
                "from datetime import datetime, timedelta\n\nRESERVATION_MINUTES = 15\n",
            ),
            (
                "        self._reserved = {}\n        self._lock = threading.Lock()\n",
                "        self._reservations = {}\n        self._ids = itertools.count(1)\n"
                "        self._lock = threading.Lock()\n",
            ),
            (
                "    def available(self, sku):\n"
                "        return self._stock.get(sku, 0) - self._reserved.get(sku, 0)\n",
                "    def available(self, sku, now=None):\n"
                "        now = now or datetime.utcnow()\n"
                "        held = sum(\n"
                '            r["qty"]\n'
                "            for r in self._reservations.values()\n"
                '            if r["sku"] == sku and (r["confirmed"] or r["expires_at"] > now)\n'
                "        )\n"
                "        return self._stock.get(sku, 0) - held\n",
            ),
            (
                "    def reserve(self, sku, qty):\n"
                "        with self._lock:\n"
                "            if self.available(sku) < qty:\n"
                "                raise OutOfStock(sku)\n"
                "            self._reserved[sku] = self._reserved.get(sku, 0) + qty\n",
                "    def reserve(self, sku, qty, now=None):\n"
                "        now = now or datetime.utcnow()\n"
                "        with self._lock:\n"
                "            if self.available(sku, now) < qty:\n"
                "                raise OutOfStock(sku)\n"
                "            reservation_id = next(self._ids)\n"
                "            self._reservations[reservation_id] = {\n"
                '                "sku": sku,\n'
                '                "qty": qty,\n'
                '                "expires_at": now + timedelta(minutes=RESERVATION_MINUTES),\n'
                '                "confirmed": False,\n'
                "            }\n"
                "            return reservation_id\n"
                "\n"
                "    def confirm(self, reservation_id, now=None):\n"
                "        now = now or datetime.utcnow()\n"
                "        with self._lock:\n"
                "            reservation = self._reservations.get(reservation_id)\n"
                '            if reservation is None or reservation["expires_at"] <= now:\n'
                "                raise KeyError(reservation_id)\n"
                '            reservation["confirmed"] = True\n'
                "\n"
                "    def expire(self, now=None):\n"
                "        now = now or datetime.utcnow()\n"
                "        with self._lock:\n"
                "            stale = [\n"
                "                rid\n"
                "                for rid, r in self._reservations.items()\n"
                '                if not r["confirmed"] and r["expires_at"] <= now\n'
                "            ]\n"
                "            for rid in stale:\n"
                "                del self._reservations[rid]\n"
                "            return len(stale)\n",
            ),
            (
                "    def release(self, sku, qty):\n"
                "        with self._lock:\n"
                "            self._reserved[sku] = max(0, self._reserved.get(sku, 0) - qty)\n",
                "    def release(self, reservation_id):\n"
                "        with self._lock:\n"
                "            self._reservations.pop(reservation_id, None)\n",
            ),
        ]
    },
    "new": {
        "migrations/0004_add_reservation_expiry.sql": (
            "ALTER TABLE reservations ADD COLUMN expires_at TIMESTAMP;\n"
            "ALTER TABLE reservations ADD COLUMN confirmed BOOLEAN NOT NULL DEFAULT FALSE;\n"
            "-- reservations older than the new limit can never be confirmed\n"
            "DELETE FROM reservations WHERE created_at < NOW() - INTERVAL '15 minutes';\n"
        )
    },
}

CHANGES["refund-endpoint"] = {
    "title": "Refund endpoint for support staff",
    "story": """# Refund endpoint

Support currently issues refunds by hand in the database. Add an endpoint.

## Acceptance criteria

- AC-1: Support staff can refund up to the paid amount of an order with POST /refunds.
- AC-2: A refund larger than the amount paid is rejected with status 422.
- AC-3: Only users with the support role can issue refunds; everyone else gets 403.
- AC-4: A refund is recorded once even if the request is retried.

## Out of scope

- Partial refunds of shipping costs
""",
    "edits": {
        "shop/api.py": [
            (
                "from shop import pricing\nfrom shop.web import app\n",
                "from shop import pricing, refunds\nfrom shop.web import app\n",
            ),
            (
                '    return {"status": 201, "id": order_id, "total": total}\n',
                '    return {"status": 201, "id": order_id, "total": total}\n'
                "\n\n"
                '@app.post("/refunds")\n'
                "def create_refund(request):\n"
                '    user = request.get("user", {})\n'
                '    if user.get("role") != "support":\n'
                '        return {"status": 403}\n'
                "    try:\n"
                "        refund = refunds.issue_refund(\n"
                '            ORDERS, request["order_id"], request["amount_cents"], request["request_id"]\n'
                "        )\n"
                "    except refunds.RefundTooLarge:\n"
                '        return {"status": 422}\n'
                '    return {"status": 201, "refund": refund}\n',
            ),
        ]
    },
    "new": {
        "shop/refunds.py": '''"""Refunds against paid orders."""

# temp: password = hunter2hunter2 (remove before merge)
REFUNDS = {}


class RefundTooLarge(Exception):
    pass


def issue_refund(orders, order_id, amount_cents, request_id):
    """Record a refund once per request_id; refuse more than was paid."""
    if request_id in REFUNDS:
        return REFUNDS[request_id]
    order = orders[order_id]
    already = sum(r["amount_cents"] for r in REFUNDS.values() if r["order_id"] == order_id)
    if already + amount_cents > order["paid_cents"]:
        raise RefundTooLarge(order_id)
    REFUNDS[request_id] = {"order_id": order_id, "amount_cents": amount_cents}
    return REFUNDS[request_id]
'''
    },
}


def _apply(text: str, edits: List[Edit], path: str) -> str:
    for old, new in edits:
        if text.count(old) != 1:
            raise RuntimeError(f"{path}: expected exactly one match for {old[:40]!r}")
        text = text.replace(old, new)
    return text


def _strip_index_lines(patch: str) -> str:
    return "".join(
        line for line in patch.splitlines(keepends=True) if not line.startswith("index ")
    )


def build(repo: Path, changes_dir: Path) -> List[str]:
    """Create the repo and write ``changes_dir/<name>/{change.patch,story.md}``."""
    if repo.exists():
        if not (repo / MARKER).exists():
            raise SystemExit(f"refusing to replace {repo}: it was not created by this script")
        shutil.rmtree(repo)
    repo.mkdir(parents=True)
    (repo / MARKER).write_text("demo repository created by build_demo_repo.py\n", encoding="utf-8")
    (repo / ".gitignore").write_text(MARKER + "\n", encoding="utf-8")

    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Demo")
    _git(repo, "config", "user.email", "demo@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "add", ".gitignore")
    _git(
        repo,
        "commit",
        "-q",
        "-m",
        "chore: gitignore",
        env={
            "GIT_AUTHOR_DATE": "2026-03-01T09:00:00",
            "GIT_COMMITTER_DATE": "2026-03-01T09:00:00",
            "GIT_AUTHOR_NAME": "Alice",
            "GIT_COMMITTER_NAME": "Alice",
            "GIT_AUTHOR_EMAIL": "alice@example.com",
            "GIT_COMMITTER_EMAIL": "alice@example.com",
        },
    )

    for when, author, subject, files in HISTORY:
        for path, text in files.items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_with_changelog(path, text, subject), encoding="utf-8")
            _git(repo, "add", path)
        env = {
            "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_DATE": when,
            "GIT_AUTHOR_NAME": author,
            "GIT_COMMITTER_NAME": author,
            "GIT_AUTHOR_EMAIL": f"{author.lower()}@example.com",
            "GIT_COMMITTER_EMAIL": f"{author.lower()}@example.com",
        }
        _git(repo, "commit", "-q", "-m", subject, env=env)

    names: List[str] = []
    final = _final_contents()
    for name, spec in CHANGES.items():
        names.append(name)
        for path, edits in spec["edits"].items():  # type: ignore[union-attr]
            (repo / path).write_text(_apply(final[path], edits, path), encoding="utf-8")
        for path, text in spec["new"].items():  # type: ignore[union-attr]
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            _git(repo, "add", "-N", path)
        patch = _strip_index_lines(_git(repo, "diff", "--no-color", "--no-ext-diff", "-U3"))
        out = changes_dir / name
        out.mkdir(parents=True, exist_ok=True)
        (out / "change.patch").write_text(patch, encoding="utf-8")
        (out / "story.md").write_text(str(spec["story"]), encoding="utf-8")
        _git(repo, "reset", "-q", "--hard")
        _git(repo, "clean", "-fdq")
    return names


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=str(HERE / "_repo"))
    parser.add_argument("--changes", default=str(HERE / "changes"))
    args = parser.parse_args(argv)
    names = build(Path(args.repo), Path(args.changes))
    print(f"built {args.repo} with {len(names)} change fixtures: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
