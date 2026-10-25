"""Deterministic raw data for the shopflow warehouse: customers, orders, payments.

Tables are lists of dicts with string values — raw data lands as text and the staging
models cast it, which is what lets format changes upstream break the pipeline.
"""

from __future__ import annotations

import csv
import io
import random
from datetime import date, timedelta

Table = list[dict[str, str]]

FIRST = ["Lena", "Jonas", "Mia", "Lukas", "Emma", "Noah", "Sofia", "Elias", "Hannah", "Paul",
         "Clara", "Felix", "Marie", "Leon", "Anna", "Ben", "Julia", "Finn", "Laura", "Tim"]
LAST = ["Fischer", "Weber", "Meyer", "Wagner", "Becker", "Schulz", "Hoffmann", "Koch",
        "Richter", "Klein", "Wolf", "Neumann", "Schwarz", "Braun", "Zimmermann", "Hartmann"]
COUNTRIES = ["DE", "FR", "NL", "AT", "ES", "IT", "PL"]
STATUSES = ["placed", "shipped", "completed", "returned", "cancelled"]
METHODS = ["card", "paypal", "bank_transfer", "gift_card"]


def generate(seed: int = 7) -> dict[str, Table]:
    rng = random.Random(seed)
    customers: Table = []
    for cid in range(1, 301):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        created = date(2024, 1, 1) + timedelta(days=rng.randint(0, 540))
        email = f"{first}.{last}{cid}@mail.example"
        customers.append({
            "customer_id": str(cid),
            "full_name": f"{first} {last}",
            "email": email.upper() if rng.random() < 0.1 else email,
            "country": rng.choice(COUNTRIES),
            "created_at": created.isoformat(),
            "updated_at": f"{created.isoformat()} 09:00:00",
        })

    orders: Table = []
    payments: Table = []
    pay_seq = 1
    for oid in range(10001, 12001):
        status = rng.choices(STATUSES, weights=[5, 10, 70, 8, 7])[0]
        order_date = date(2025, 1, 1) + timedelta(days=rng.randint(0, 270))
        orders.append({"order_id": str(oid), "customer_id": str(rng.randint(1, 300)),
                       "order_date": order_date.isoformat(), "status": status})
        if status == "cancelled":
            continue
        for _ in range(2 if rng.random() < 0.1 else 1):
            payments.append({
                "payment_id": f"P{pay_seq:05d}",
                "order_id": str(oid),
                "method": rng.choices(METHODS, weights=[55, 25, 15, 5])[0],
                "amount": f"{rng.uniform(5, 400):.2f}",
            })
            pay_seq += 1
    return {"raw_customers": customers, "raw_orders": orders, "raw_payments": payments}


def to_csv(table: Table) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(table[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(table)
    return buf.getvalue()
