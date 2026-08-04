"""
Order Simulator
==================
Simulates "live" store activity so you can actually test and demo the
"stock drops -> agent reacts" flow, since there's no real e-commerce
website sending real orders into this database.

Three simulation actions:
  1. simulate_order() -- places a fake order, reduces stock_qty, adds to
     today's sales_history. This is what a real order would do to the
     database in production.
  2. simulate_return() -- creates a pending return request, so you can
     demo the Returns Agent computing a live risk score for it.
  3. simulate_demand_drop() -- artificially lowers a product's recent
     sales history, so you can demonstrate the Pricing Agent's discount
     logic triggering live.

All three are ONLY for testing/demo purposes -- clearly separated from
the real agent logic, never called automatically.
"""

import sqlite3
import random
from datetime import datetime, timedelta


def simulate_order(conn, product_id, quantity, customer_id=None):
    """
    Simulates a real customer placing an order right now:
    - Reduces the product's stock_qty
    - Adds a new row to orders (today's date)
    - Adds the quantity to today's sales_history row (creates it if it
      doesn't exist yet for today)

    Returns a dict summarizing what happened, or raises ValueError if
    there isn't enough stock.
    """
    cur = conn.cursor()

    product = cur.execute(
        "SELECT stock_qty, price, name FROM products WHERE product_id = ?", (product_id,)
    ).fetchone()
    if product is None:
        raise ValueError(f"Product {product_id} not found")

    current_stock, price, name = product
    if quantity > current_stock:
        raise ValueError(f"Not enough stock: only {current_stock} units available")

    if customer_id is None:
        all_customers = [row[0] for row in cur.execute("SELECT customer_id FROM customers").fetchall()]
        customer_id = random.choice(all_customers)

    today = datetime.now().date().isoformat()
    total_amount = round(price * quantity, 2)

    new_stock = current_stock - quantity
    cur.execute("UPDATE products SET stock_qty = ? WHERE product_id = ?", (new_stock, product_id))

    cur.execute(
        """INSERT INTO orders (customer_id, product_id, order_date, quantity, total_amount, status)
           VALUES (?, ?, ?, ?, ?, 'completed')""",
        (customer_id, product_id, today, quantity, total_amount),
    )

    existing = cur.execute(
        "SELECT sale_id, units_sold FROM sales_history WHERE product_id = ? AND sale_date = ?",
        (product_id, today),
    ).fetchone()

    if existing:
        sale_id, existing_units = existing
        cur.execute(
            "UPDATE sales_history SET units_sold = ? WHERE sale_id = ?",
            (existing_units + quantity, sale_id),
        )
    else:
        cur.execute(
            """INSERT INTO sales_history (product_id, sale_date, units_sold, is_promo, is_stockout)
               VALUES (?, ?, ?, 0, 0)""",
            (product_id, today, quantity),
        )

    conn.commit()

    return {
        "product_name": name,
        "quantity": quantity,
        "old_stock": current_stock,
        "new_stock": new_stock,
        "total_amount": total_amount,
    }


def simulate_return(conn, product_id, reason, days_since_order=15, customer_id=None):
    """
    Simulates a customer returning a product right now:
    - Creates a backdated order (so days_since_order is realistic)
    - Inserts a new row into returns, with agent_decision left NULL so it
      shows up as pending on the Returns dashboard page for live review

    NOTE: is_flagged is set to 0 as a placeholder on insert -- it is NOT
    used by the live Returns Agent (which always recomputes risk fresh
    from the trained model at review time). That column only matters
    during model TRAINING, not live agent decisions.
    """
    cur = conn.cursor()

    product = cur.execute("SELECT price, name FROM products WHERE product_id = ?", (product_id,)).fetchone()
    if product is None:
        raise ValueError(f"Product {product_id} not found")
    price, name = product

    if customer_id is None:
        all_customers = [row[0] for row in cur.execute("SELECT customer_id FROM customers").fetchall()]
        customer_id = random.choice(all_customers)

    quantity = random.randint(1, 3)
    total_amount = round(price * quantity, 2)
    order_date = (datetime.now().date() - timedelta(days=days_since_order)).isoformat()

    cur.execute(
        """INSERT INTO orders (customer_id, product_id, order_date, quantity, total_amount, status)
           VALUES (?, ?, ?, ?, ?, 'returned')""",
        (customer_id, product_id, order_date, quantity, total_amount),
    )
    order_id = cur.lastrowid

    prior_returns = cur.execute(
        "SELECT COUNT(*) FROM returns WHERE customer_id = ?", (customer_id,)
    ).fetchone()[0]
    customer_total_returns = prior_returns + 1

    return_date = datetime.now().date().isoformat()
    cur.execute(
        """INSERT INTO returns
           (order_id, customer_id, product_id, return_date, reason,
            days_since_order, customer_total_returns, is_flagged)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
        (order_id, customer_id, product_id, return_date, reason, days_since_order, customer_total_returns),
    )
    conn.commit()

    return {
        "product_name": name,
        "customer_id": customer_id,
        "days_since_order": days_since_order,
        "customer_total_returns": customer_total_returns,
        "reason": reason,
        "return_id": cur.lastrowid,
    }


def simulate_demand_drop(conn, product_id, days=10, reduction_pct=60):
    """
    DEMO-ONLY TOOL: artificially reduces a product's units_sold over the
    last N days in sales_history, so you can trigger and show the Pricing
    Agent's "overstocked + falling demand" discount logic live.

    This does NOT reflect real customer behavior -- it's purely to make
    the demo controllable rather than waiting for the random dataset to
    naturally produce this scenario. State this clearly if you use it in
    your viva demo.
    """
    cur = conn.cursor()
    cutoff_date = (datetime.now().date() - timedelta(days=days)).isoformat()

    rows = cur.execute(
        "SELECT sale_id, units_sold FROM sales_history WHERE product_id = ? AND sale_date >= ?",
        (product_id, cutoff_date),
    ).fetchall()

    for sale_id, units_sold in rows:
        reduced = max(0, int(units_sold * (1 - reduction_pct / 100)))
        cur.execute("UPDATE sales_history SET units_sold = ? WHERE sale_id = ?", (reduced, sale_id))

    conn.commit()
    return {"rows_updated": len(rows), "reduction_pct": reduction_pct}