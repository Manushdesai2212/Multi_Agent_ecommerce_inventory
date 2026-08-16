"""
Synthetic Data Generator (FINAL VERSION v3)
===============================================
Builds on the realistic v2 generator (stockouts, shocks, category-specific
patterns, probabilistic return flagging). This version fixes:

1. is_promo / is_stockout are now recorded per product-day in sales_history
   (previously computed but discarded).
2. Orders are now sampled PROPORTIONALLY to actual daily sales volume from
   sales_history, instead of being a fully disconnected random process.
   (Not an exact-sum reconciliation -- that would require 100K+ order rows,
   which contradicts having a manageable, browsable orders/returns table.
   This is a statistical link: busier product-days produce proportionally
   more orders, which is the realistic and honest middle ground.)
3. Faker is now seeded for full reproducibility, matching random/numpy.
4. is_repeat_offender is now persisted on the customers table, not just
   held as an internal Python variable during generation.
5. NUM_ORDERS and return rates are UNCHANGED from the validated v2 version
   (6000 orders, category-based return rates) -- no reason to revert this,
   it was a deliberate fix for small-data instability.
"""

import sqlite3
import random
import os
import numpy as np
from datetime import datetime, timedelta
from faker import Faker

fake = Faker()
fake.seed_instance(42)   # <-- NEW: seeds Faker's own generator for full reproducibility
random.seed(42)
np.random.seed(42)

DB_PATH = "data/ecommerce.db"
SCHEMA_PATH = "schema.sql"

NUM_PRODUCTS = 80
NUM_CUSTOMERS = 150
DAYS_OF_HISTORY = 365
NUM_ORDERS = 6000          # unchanged, as instructed
REPEAT_OFFENDER_RATE = 0.05

CATEGORIES = {
    "Electronics":    (500, 15000),
    "Apparel":        (300, 3000),
    "Home & Kitchen": (300, 6000),
    "Beauty":         (150, 2000),
    "Sports":         (400, 5000),
}

CATEGORY_WEEKEND_EFFECT = {
    "Electronics":    1.15,
    "Apparel":        1.45,
    "Home & Kitchen": 1.20,
    "Beauty":         1.30,
    "Sports":         1.55,
}

CATEGORY_RETURN_RATE = {
    "Apparel":        0.16,
    "Beauty":         0.13,
    "Electronics":    0.05,
    "Home & Kitchen": 0.06,
    "Sports":         0.08,
}

# Must match returns_agent.py's CATEGORY_RETURN_WINDOW_DAYS -- used here to
# generate REALISTIC return timing, since most real return requests happen
# within the policy window (that's when customers are even able to submit
# one); only a small minority arrive late.
CATEGORY_RETURN_WINDOW = {
    "Electronics":    7,
    "Apparel":        15,
    "Home & Kitchen": 7,
    "Beauty":         7,
    "Sports":         7,
}

SHOCK_PROBABILITY = 0.03
PROMO_PROBABILITY = 0.02

RETURN_REASONS = [
    "Item damaged on arrival",
    "Wrong size / doesn't fit",
    "Changed my mind",
    "Item not as described",
    "Received wrong item",
    "Found cheaper elsewhere",
    "Quality not as expected",
]

# How much each reason contributes to risk -- reasons that are clearly the
# seller's fault (damaged, wrong item shipped) are treated as legitimate
# and add no risk. Discretionary reasons (buyer's remorse, price shopping)
# add meaningfully more risk, since they're the reasons most associated
# with policy abuse in real return data.
REASON_RISK_WEIGHT = {
    "Item damaged on arrival":  0.0,
    "Received wrong item":      0.0,
    "Wrong size / doesn't fit": 0.2,
    "Item not as described":    0.3,
    "Quality not as expected":  0.3,
    "Changed my mind":          1.2,
    "Found cheaper elsewhere":  1.5,
}


def build_database():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    with open(SCHEMA_PATH, "r") as f:
        cur.executescript(f.read())
    conn.commit()
    return conn


def generate_products(conn):
    cur = conn.cursor()
    products = []
    for _ in range(NUM_PRODUCTS):
        category = random.choice(list(CATEGORIES.keys()))
        low, high = CATEGORIES[category]
        price = round(random.uniform(low, high), 2)
        initial_stock = random.randint(150, 400)
        reorder_level = max(5, int(initial_stock * 0.15))
        name = f"{fake.word().capitalize()} {category[:-1] if category.endswith('s') else category}"
        created_at = fake.date_between(start_date="-2y", end_date="-6M").isoformat()

        cur.execute(
            """INSERT INTO products (name, category, price, stock_qty, reorder_level, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, category, price, initial_stock, reorder_level, created_at),
        )
        products.append(cur.lastrowid)
    conn.commit()
    return products


def generate_customers(conn):
    """
    Repeat-offender status is decided here and WRITTEN to the customers
    table (is_repeat_offender column) -- previously this was only tracked
    as an internal Python set during return generation, invisible to any
    later SQL query or agent.
    """
    cur = conn.cursor()
    customer_ids = []
    for _ in range(NUM_CUSTOMERS):
        name = fake.name()
        email = fake.email()
        signup_date = fake.date_between(start_date="-2y", end_date="today").isoformat()
        cur.execute(
            "INSERT INTO customers (name, email, signup_date) VALUES (?, ?, ?)",
            (name, email, signup_date),
        )
        customer_ids.append(cur.lastrowid)
    conn.commit()

    num_repeat_offenders = max(1, int(NUM_CUSTOMERS * REPEAT_OFFENDER_RATE))
    repeat_offenders = set(random.sample(customer_ids, num_repeat_offenders))

    cur.executemany(
        "UPDATE customers SET is_repeat_offender = 1 WHERE customer_id = ?",
        [(cid,) for cid in repeat_offenders],
    )
    conn.commit()

    return customer_ids, repeat_offenders


def generate_sales_history(conn, product_ids):
    """
    Simulates daily sales with stockout capping, random shocks, promo
    spikes, and category-specific weekend effects.

    Returns `daily_rows`: a list of dicts with full detail per product-day
    (including actual_sold) so generate_orders_and_returns() can sample
    orders WEIGHTED by real sales volume, instead of as a disconnected
    random process.
    """
    cur = conn.cursor()

    cur.execute("SELECT product_id, category, stock_qty, reorder_level FROM products")
    product_info = {row[0]: {"category": row[1], "stock": row[2], "reorder_level": row[3]} for row in cur.fetchall()}

    today = datetime.now().date()
    start_date = today - timedelta(days=DAYS_OF_HISTORY)

    trend_choices = ["up", "down", "flat"]
    trend_weights = [0.2, 0.15, 0.65]
    product_trends = {pid: random.choices(trend_choices, trend_weights)[0] for pid in product_ids}
    base_demand = {pid: random.uniform(2, 25) for pid in product_ids}

    insert_rows = []       # for the sales_history table insert
    daily_rows = []        # kept in memory for weighted order sampling below

    for day_offset in range(DAYS_OF_HISTORY):
        current_date = start_date + timedelta(days=day_offset)
        weekday = current_date.weekday()
        is_weekend = weekday >= 5
        seasonal_multiplier = 1.6 if 300 <= day_offset <= 330 else 1.0
        progress = day_offset / DAYS_OF_HISTORY

        for pid in product_ids:
            info = product_info[pid]
            category = info["category"]
            trend = product_trends[pid]

            weekend_mult = CATEGORY_WEEKEND_EFFECT.get(category, 1.2) if is_weekend else 1.0

            if trend == "up":
                trend_mult = 1.0 + progress * 0.8
            elif trend == "down":
                trend_mult = 1.0 - progress * 0.5
            else:
                trend_mult = 1.0

            expected = base_demand[pid] * weekend_mult * seasonal_multiplier * trend_mult

            is_promo = 0
            if random.random() < SHOCK_PROBABILITY:
                shock_mult = random.choice([random.uniform(0.15, 0.4), random.uniform(2.0, 3.5)])
                expected *= shock_mult

            if random.random() < PROMO_PROBABILITY:
                expected *= random.uniform(1.5, 2.2)
                is_promo = 1   # <-- NEW: record that a promo was applied this product-day

            noise_std = max(1.0, expected * 0.35)
            intended_demand = max(0, int(np.random.normal(expected, noise_std)))

            available_stock = info["stock"]
            actual_sold = min(intended_demand, available_stock)
            is_stockout = 1 if actual_sold < intended_demand else 0   # <-- NEW: record stockout capping

            info["stock"] -= actual_sold

            if info["stock"] <= info["reorder_level"]:
                restock_amount = random.randint(100, 350)
                info["stock"] += restock_amount

            insert_rows.append((pid, current_date.isoformat(), actual_sold, is_promo, is_stockout))
            daily_rows.append({
                "product_id": pid,
                "date": current_date.isoformat(),
                "units_sold": actual_sold,
            })

    cur.executemany(
        """INSERT INTO sales_history (product_id, sale_date, units_sold, is_promo, is_stockout)
           VALUES (?, ?, ?, ?, ?)""",
        insert_rows,
    )

    for pid, info in product_info.items():
        cur.execute("UPDATE products SET stock_qty = ? WHERE product_id = ?", (info["stock"], pid))

    conn.commit()
    return daily_rows


def generate_orders_and_returns(conn, product_ids, customer_ids, repeat_offenders, daily_rows):
    """
    Orders are now sampled WEIGHTED by each product-day's actual units_sold
    from sales_history (daily_rows), instead of being generated as a fully
    independent random process. Product-days with more real sales are
    proportionally more likely to produce an order -- and zero-sale days
    (including stockout days) essentially never do, which is realistic.

    This is a statistical link, not an exact-sum reconciliation: NUM_ORDERS
    stays at 6000 as instructed, it is not forced to equal total units_sold
    (which would require 100,000+ rows and isn't useful for this project).
    """
    cur = conn.cursor()

    customer_return_counts = {cid: 0 for cid in customer_ids}

    price_lookup = dict(cur.execute("SELECT product_id, price FROM products").fetchall())
    category_lookup = dict(cur.execute("SELECT product_id, category FROM products").fetchall())

    # Build weighted sampling pool from actual daily sales volume.
    # A tiny epsilon avoids fully excluding zero-sale days while still
    # making them very unlikely to be chosen.
    weights = np.array([max(row["units_sold"], 0.01) for row in daily_rows], dtype=float)
    weights = weights / weights.sum()

    sampled_indices = np.random.choice(len(daily_rows), size=NUM_ORDERS, p=weights, replace=True)

    for idx in sampled_indices:
        row = daily_rows[idx]
        product_id = row["product_id"]
        order_date_str = row["date"]

        customer_id = random.choice(customer_ids)
        quantity = random.randint(1, 4)
        total_amount = round(price_lookup[product_id] * quantity, 2)
        category = category_lookup[product_id]

        base_rate = CATEGORY_RETURN_RATE.get(category, 0.08)
        is_repeat_offender = customer_id in repeat_offenders
        return_chance = base_rate * (2.5 if is_repeat_offender else 1)
        gets_returned = random.random() < return_chance

        status = "returned" if gets_returned else "completed"
        cur.execute(
            """INSERT INTO orders (customer_id, product_id, order_date, quantity, total_amount, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (customer_id, product_id, order_date_str, quantity, total_amount, status),
        )
        order_id = cur.lastrowid

        if gets_returned:
            order_date = datetime.fromisoformat(order_date_str).date()
            # Realistic timing: most return requests happen WITHIN the
            # category's actual policy window (that's when customers are
            # even able to submit one) -- only a minority arrive late.
            # An exponential distribution scaled to ~55% of the window
            # naturally produces this shape: most mass under the window,
            # a smaller decaying tail beyond it (a late-but-plausible
            # request, or someone asking for a goodwill exception).
            window = CATEGORY_RETURN_WINDOW.get(category, 7)
            days_since_order = max(1, int(random.expovariate(1 / (window * 0.55))))
            return_date = order_date + timedelta(days=days_since_order)
            reason = random.choice(RETURN_REASONS)

            customer_return_counts[customer_id] += 1
            total_returns_so_far = customer_return_counts[customer_id]

            # days_past_window directly captures POLICY VIOLATION SEVERITY --
            # a much more meaningful signal than raw days_since_order alone,
            # since "10 days for a 7-day window" (3 days over) is very
            # different from "10 days for a 15-day window" (still within).
            days_past_window = max(0, days_since_order - window)

            risk_score = (
                0.18 * days_past_window        # strong weight -- how far past policy, not just raw days
                + 0.015 * days_since_order      # small residual weight on raw timing too
                + 0.5 * total_returns_so_far
                + (1.5 if is_repeat_offender else 0)
                + REASON_RISK_WEIGHT.get(reason, 0.5)
                + random.gauss(0, 0.8)
            )
            flag_probability = 1 / (1 + np.exp(-(risk_score - 3)))
            is_flagged = int(random.random() < flag_probability)

            # Realistic historical state: a real business processes returns
            # continuously, so only recent returns (last 7 days) should
            # still be sitting in a "pending" queue. Older returns get a
            # simulated historical decision -- mostly approved, with
            # flagged returns somewhat more likely to have been rejected.
            # This keeps the Returns dashboard page and "pending" count
            # realistic instead of showing hundreds of months-old backlog.
            days_ago = (datetime.now().date() - return_date).days
            if days_ago <= 30:
                agent_decision = None  # genuinely pending -- shows up for live review
            else:
                if is_flagged:
                    agent_decision = random.choices(["approved", "rejected"], weights=[0.5, 0.5])[0]
                else:
                    agent_decision = random.choices(["approved", "rejected"], weights=[0.92, 0.08])[0]

            cur.execute(
                """INSERT INTO returns
                   (order_id, customer_id, product_id, return_date, reason,
                    days_since_order, customer_total_returns, is_flagged, agent_decision)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (order_id, customer_id, product_id, return_date.isoformat(), reason,
                 days_since_order, total_returns_so_far, is_flagged, agent_decision),
            )

    conn.commit()


def print_summary(conn):
    cur = conn.cursor()
    print("\n--- Synthetic dataset generated: data/ecommerce.db ---")
    for table in ["products", "customers", "sales_history", "orders", "returns"]:
        count = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:15s}: {count:,} rows")

    flagged = cur.execute("SELECT COUNT(*) FROM returns WHERE is_flagged = 1").fetchone()[0]
    total_returns = cur.execute("SELECT COUNT(*) FROM returns").fetchone()[0]
    print(f"\n  Flagged (suspicious) returns: {flagged} / {total_returns}")

    zero_sale_days = cur.execute("SELECT COUNT(*) FROM sales_history WHERE units_sold = 0").fetchone()[0]
    total_sale_rows = cur.execute("SELECT COUNT(*) FROM sales_history").fetchone()[0]
    print(f"  Zero-sale days: {zero_sale_days} / {total_sale_rows}")

    stockout_days = cur.execute("SELECT COUNT(*) FROM sales_history WHERE is_stockout = 1").fetchone()[0]
    promo_days = cur.execute("SELECT COUNT(*) FROM sales_history WHERE is_promo = 1").fetchone()[0]
    print(f"  Stockout days: {stockout_days} / {total_sale_rows}")
    print(f"  Promo days: {promo_days} / {total_sale_rows}")

    repeat_offenders_count = cur.execute("SELECT COUNT(*) FROM customers WHERE is_repeat_offender = 1").fetchone()[0]
    print(f"  Repeat offenders (persisted): {repeat_offenders_count} / {NUM_CUSTOMERS}")
    print("-------------------------------------------------------\n")


if __name__ == "__main__":
    conn = build_database()
    print("Building schema... done")

    product_ids = generate_products(conn)
    print(f"Generated {len(product_ids)} products")

    customer_ids, repeat_offenders = generate_customers(conn)
    print(f"Generated {len(customer_ids)} customers ({len(repeat_offenders)} marked repeat offenders)")

    daily_rows = generate_sales_history(conn, product_ids)
    print(f"Generated {DAYS_OF_HISTORY} days of sales history (with is_promo / is_stockout recorded)")

    generate_orders_and_returns(conn, product_ids, customer_ids, repeat_offenders, daily_rows)
    print(f"Generated {NUM_ORDERS} orders, sampled proportionally to real daily sales volume")

    print_summary(conn)
    conn.close()