"""Pipeline incidents: what breaks, the category an on-call engineer should name, a reference
fix that proves the incident is solvable, and hidden acceptance checks.

Acceptance checks are computed in Python from the raw tables — not from the dbt models — so a
"fix" that makes the build green but changes the numbers (dropping rows, wrong units, deleting
duplicates that were real) still fails.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from pipeline_triage.data import Table
from pipeline_triage.workspace import Workspace

CATEGORIES = {
    "schema_drift": "an upstream table's columns changed (renamed, split, removed, new units)",
    "format_change": "values arrive in a new format or casing that the casts or filters reject",
    "new_category_value": "a legitimate new value appears in a field with a fixed set of values",
    "duplicate_records": "the same record arrives more than once",
    "referential_integrity": "rows reference parents that do not exist",
    "logic_bug": "a recent change to a model's SQL broke its results",
}

Tables = dict[str, Table]
Check = tuple[str, bool]

BASE_TICKET = (
    "The nightly `dbt build` for the shopflow warehouse failed. Find the root cause, fix it in "
    "the dbt project, get `dbt build` passing, and submit your diagnosis. Raw data in seeds/ is "
    "owned by upstream teams: do not edit it. Do not delete, disable or weaken tests unless the "
    "business rule they encode has genuinely changed."
)


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    title: str
    fix: list[tuple[str, str, str]]
    accept: Callable[[Workspace, Tables, Tables], list[Check]]
    data: Callable[[Tables, random.Random], None] | None = None
    code: list[tuple[str, str, str]] = field(default_factory=list)
    context: str = ""

    @property
    def ticket(self) -> str:
        return BASE_TICKET + (f"\n\nContext from the team channel: {self.context}"
                              if self.context else "")


# --- golden values from raw data ----------------------------------------------------------

def completed_revenue(t: Tables) -> Decimal:
    status = {o["order_id"]: o["status"] for o in t["raw_orders"]}
    seen: set[str] = set()
    total = Decimal(0)
    for p in t["raw_payments"]:
        if p["payment_id"] in seen or status.get(p["order_id"]) != "completed":
            continue
        seen.add(p["payment_id"])
        total += Decimal(p["amount"])
    return total


def _close(a, b) -> bool:
    return a is not None and abs(Decimal(str(a)) - Decimal(str(b))) <= Decimal("0.01")


def revenue_matches(ws: Workspace, clean: Tables, _: Tables) -> list[Check]:
    got = ws.scalar("select sum(revenue) from revenue_daily")
    return [(f"completed revenue {got} == {completed_revenue(clean)}",
             _close(got, completed_revenue(clean)))]


def orders_complete(ws: Workspace, clean: Tables, _: Tables) -> list[Check]:
    n = ws.scalar("select count(*) from fct_orders")
    return [(f"fct_orders has {n} rows, expected {len(clean['raw_orders'])}",
             n == len(clean["raw_orders"]))]


def both(*checks: Callable) -> Callable:
    return lambda ws, clean, raw: [c for chk in checks for c in chk(ws, clean, raw)]


# --- data mutations --------------------------------------------------------------------

def rename_column(table: str, old: str, new: str, transform=lambda v: v):
    def mutate(t: Tables, _: random.Random) -> None:
        t[table] = [{(new if k == old else k): (transform(v) if k == old else v)
                     for k, v in row.items()} for row in t[table]]
    return mutate


def split_name(t: Tables, _: random.Random) -> None:
    rows = []
    for row in t["raw_customers"]:
        first, last = row["full_name"].split(" ", 1)
        rows.append({"customer_id": row["customer_id"], "first_name": first, "last_name": last,
                     **{k: v for k, v in row.items() if k not in ("customer_id", "full_name")}})
    t["raw_customers"] = rows


def eu_dates_for_new_batch(t: Tables, rng: random.Random) -> None:
    for row in rng.sample(t["raw_orders"], 150):
        y, m, d = row["order_date"].split("-")
        row["order_date"] = f"{d}/{m}/{y}"


def euro_amounts_for_new_provider(t: Tables, rng: random.Random) -> None:
    for row in rng.sample(t["raw_payments"], 120):
        row["amount"] = "€" + row["amount"].replace(".", ",")


def uppercase_status_batch(t: Tables, rng: random.Random) -> None:
    for row in rng.sample(t["raw_orders"], 200):
        row["status"] = row["status"].upper()


PARTIAL_RETURNS_LAUNCH = "2025-09-08"
APPLE_PAY_LAUNCH = "2025-09-15"


def new_status(t: Tables, rng: random.Random) -> None:
    """Partial returns exist only for orders placed after the launch date."""
    eligible = [r for r in t["raw_orders"] if r["order_date"] >= PARTIAL_RETURNS_LAUNCH
                and r["status"] in ("completed", "returned")]
    for row in rng.sample(eligible, 40):
        row["status"] = "partially_returned"


def new_method(t: Tables, _: random.Random) -> None:
    """Card payments on orders placed after the Apple Pay launch become apple_pay."""
    recent = {o["order_id"] for o in t["raw_orders"] if o["order_date"] >= APPLE_PAY_LAUNCH}
    for row in t["raw_payments"]:
        if row["order_id"] in recent and row["method"] == "card":
            row["method"] = "apple_pay"


def duplicate_rows(table: str, n: int):
    def mutate(t: Tables, rng: random.Random) -> None:
        t[table] = t[table] + [dict(r) for r in rng.sample(t[table], n)]
    return mutate


def customer_updates(t: Tables, rng: random.Random) -> None:
    for row in rng.sample(t["raw_customers"], 25):
        updated = dict(row)
        updated["email"] = row["email"].lower().replace("@mail.example", "@newmail.example")
        updated["updated_at"] = "2025-09-30 12:00:00"
        t["raw_customers"].append(updated)


def orphan_test_payments(t: Tables, rng: random.Random) -> None:
    for i in range(30):
        t["raw_payments"].append({"payment_id": f"T{i:04d}", "order_id": str(99001 + i),
                                  "method": "card", "amount": f"{rng.uniform(1, 50):.2f}"})


# --- acceptance checks that need the mutated raw data --------------------------------------

def monthly_orders_match(ws: Workspace, clean: Tables, _: Tables) -> list[Check]:
    want = Counter(o["order_date"][:7] for o in clean["raw_orders"])
    got = dict(ws.query("select strftime(order_date, '%Y-%m'), count(*) from fct_orders "
                        "group by 1", limit=100))
    nulls = ws.scalar("select count(*) from fct_orders where order_date is null")
    return [(f"orders per month match ({len(want)} months)", got == dict(want)),
            (f"{nulls} orders without a date", nulls == 0)]


def full_names_match(ws: Workspace, clean: Tables, _: Tables) -> list[Check]:
    want = {int(r["customer_id"]): r["full_name"] for r in clean["raw_customers"]}
    got = dict(ws.query("select customer_id, full_name from dim_customers", limit=1000))
    return [("full_name preserved for every customer", got == want)]


def latest_emails(ws: Workspace, _: Tables, raw: Tables) -> list[Check]:
    latest: dict[int, tuple[str, str]] = {}
    for r in raw["raw_customers"]:
        cid = int(r["customer_id"])
        if cid not in latest or r["updated_at"] > latest[cid][0]:
            latest[cid] = (r["updated_at"], r["email"].lower())
    got = dict(ws.query("select customer_id, email from dim_customers", limit=1000))
    return [("one row per customer with the latest email",
             got == {cid: email for cid, (_, email) in latest.items()})]


def new_status_kept(ws: Workspace, clean: Tables, raw: Tables) -> list[Check]:
    n = ws.scalar("select count(*) from fct_orders where status = 'partially_returned'")
    return orders_complete(ws, clean, raw) + [(f"{n} partially_returned orders kept", n == 40)]


def countries_match(ws: Workspace, clean: Tables, _: Tables) -> list[Check]:
    country = {r["customer_id"]: r["country"] for r in clean["raw_customers"]}
    want = Counter(country[o["customer_id"]] for o in clean["raw_orders"])
    got = dict(ws.query("select country, count(*) from fct_orders group by 1", limit=50))
    return [("orders per country match", got == dict(want))]


def ltv_matches(ws: Workspace, clean: Tables, _: Tables) -> list[Check]:
    got = ws.scalar("select sum(lifetime_value) from dim_customers")
    return [(f"total lifetime value {got} == completed revenue", _close(got,
                                                                      completed_revenue(clean)))]


# --- the incidents --------------------------------------------------------------------

STG_PAY = "models/staging/stg_payments.sql"
STG_ORD = "models/staging/stg_orders.sql"
STG_CUS = "models/staging/stg_customers.sql"
FCT = "models/marts/fct_orders.sql"
SCHEMA = "models/schema.yml"

SCENARIOS: list[Scenario] = [
    Scenario(
        "S01", "schema_drift", "payments.amount renamed to amount_eur",
        data=rename_column("raw_payments", "amount", "amount_eur"),
        fix=[(STG_PAY, "cast(amount as", "cast(amount_eur as")],
        accept=revenue_matches),
    Scenario(
        "S02", "schema_drift", "payments.amount replaced by integer amount_cents",
        data=rename_column("raw_payments", "amount", "amount_cents",
                           lambda v: str(int(round(float(v) * 100)))),
        fix=[(STG_PAY, "cast(amount as decimal(12, 2))",
              "cast(amount_cents as decimal(14, 2)) / 100")],
        accept=revenue_matches),
    Scenario(
        "S03", "schema_drift", "customers.full_name split into first_name and last_name",
        data=split_name,
        fix=[(STG_CUS, "    full_name,", "    first_name || ' ' || last_name as full_name,")],
        accept=full_names_match),
    Scenario(
        "S04", "format_change", "a new batch of orders has DD/MM/YYYY dates",
        data=eu_dates_for_new_batch,
        fix=[(STG_ORD, "cast(order_date as date)",
              "coalesce(try_strptime(order_date, '%Y-%m-%d'), "
              "try_strptime(order_date, '%d/%m/%Y'))::date")],
        accept=monthly_orders_match),
    Scenario(
        "S05", "format_change", "a new payment provider sends amounts like €12,50",
        data=euro_amounts_for_new_provider,
        fix=[(STG_PAY, "cast(amount as decimal(12, 2))",
              "cast(replace(replace(amount, '€', ''), ',', '.') as decimal(12, 2))")],
        accept=revenue_matches),
    Scenario(
        "S06", "format_change", "a batch of orders has upper-case statuses",
        data=uppercase_status_batch,
        fix=[(STG_ORD, "    status\n", "    lower(status) as status\n")],
        accept=revenue_matches),
    Scenario(
        "S07", "new_category_value", "partial returns launched: new order status",
        data=new_status,
        fix=[(SCHEMA, "values: [placed, shipped, completed, returned, cancelled]",
              "values: [placed, shipped, completed, returned, partially_returned, cancelled]")],
        accept=new_status_kept,
        context=f"#orders-team: partial returns went live on {PARTIAL_RETURNS_LAUNCH} - "
                "customers can now return part of an order."),
    Scenario(
        "S08", "new_category_value", "Apple Pay launched: new payment method",
        data=new_method,
        fix=[(SCHEMA, "values: [card, paypal, bank_transfer, gift_card]",
              "values: [card, paypal, bank_transfer, gift_card, apple_pay]")],
        accept=revenue_matches,
        context=f"#payments: Apple Pay went live for all EU stores on {APPLE_PAY_LAUNCH}."),
    Scenario(
        "S09", "duplicate_records", "an orders batch was delivered twice",
        data=duplicate_rows("raw_orders", 50),
        fix=[(STG_ORD, "from {{ ref('raw_orders') }}",
              "from (select distinct * from {{ ref('raw_orders') }})")],
        accept=both(revenue_matches, orders_complete)),
    Scenario(
        "S10", "duplicate_records", "some payment rows were re-sent",
        data=duplicate_rows("raw_payments", 40),
        fix=[(STG_PAY, "from {{ ref('raw_payments') }}",
              "from (select distinct * from {{ ref('raw_payments') }})")],
        accept=revenue_matches),
    Scenario(
        "S11", "duplicate_records", "customer updates arrive as new rows",
        data=customer_updates,
        fix=[(STG_CUS, "from {{ ref('raw_customers') }}",
              "from {{ ref('raw_customers') }}\n"
              "qualify row_number() over (partition by customer_id order by updated_at desc) = 1")],
        accept=latest_emails),
    Scenario(
        "S12", "referential_integrity", "test payments for orders that do not exist",
        data=orphan_test_payments,
        fix=[(STG_PAY, "from {{ ref('raw_payments') }}",
              "from {{ ref('raw_payments') }}\n"
              "where order_id in (select order_id from {{ ref('raw_orders') }})")],
        accept=revenue_matches),
    Scenario(
        "S13", "logic_bug", "payments joined without aggregation (fan-out)",
        code=[(FCT, "left join payments p on p.order_id = o.order_id",
               "left join {{ ref('stg_payments') }} p on p.order_id = o.order_id"),
              (FCT, "coalesce(p.amount_paid, 0) as amount_paid,\n    coalesce(p.n_payments, 0)",
               "coalesce(p.amount, 0) as amount_paid,\n    1")],
        fix=[(FCT, "left join {{ ref('stg_payments') }} p on p.order_id = o.order_id",
              "left join payments p on p.order_id = o.order_id"),
             (FCT, "coalesce(p.amount, 0) as amount_paid,\n    1",
              "coalesce(p.amount_paid, 0) as amount_paid,\n    coalesce(p.n_payments, 0)")],
        accept=both(revenue_matches, orders_complete)),
    Scenario(
        "S14", "logic_bug", "typo in the revenue filter",
        code=[("models/marts/revenue_daily.sql", "where status = 'completed'",
               "where status = 'complete'")],
        fix=[("models/marts/revenue_daily.sql", "where status = 'complete'",
              "where status = 'completed'")],
        accept=revenue_matches),
    Scenario(
        "S15", "logic_bug", "lifetime value counts every order",
        code=[("models/marts/dim_customers.sql",
               "sum(case when o.is_completed then o.amount_paid end)", "sum(o.amount_paid)")],
        fix=[("models/marts/dim_customers.sql", "sum(o.amount_paid)",
              "sum(case when o.is_completed then o.amount_paid end)")],
        accept=ltv_matches),
    Scenario(
        "S16", "logic_bug", "customers joined on the wrong key",
        code=[(FCT, "on c.customer_id = o.customer_id", "on c.customer_id = o.order_id")],
        fix=[(FCT, "on c.customer_id = o.order_id", "on c.customer_id = o.customer_id")],
        accept=countries_match),
]

BY_ID = {s.id: s for s in SCENARIOS}
