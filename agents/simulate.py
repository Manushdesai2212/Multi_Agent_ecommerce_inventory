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


def simulate_increase_stock(conn, product_id, add_quantity):
    """
    Demo-only helper: increase a product's stock quantity by `add_quantity`.
    Updates the `products.stock_qty` and logs the change to `agent_logs`
    (keeps an audit trail similar to other agent actions).

    Returns a dict summarizing the change.
    """
    cur = conn.cursor()

    product = cur.execute(
        "SELECT stock_qty, name FROM products WHERE product_id = ?", (product_id,)
    ).fetchone()
    if product is None:
        raise ValueError(f"Product {product_id} not found")

    current_stock, name = product
    new_stock = current_stock + int(add_quantity)
    cur.execute("UPDATE products SET stock_qty = ? WHERE product_id = ?", (new_stock, product_id))

    # Optional: add an audit row in agent_logs so UI owners can see the change
    try:
        cur.execute(
            "INSERT INTO agent_logs (timestamp, agent_name, action, details) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), "Simulator", "increase_stock", f"{{\"product_id\": {product_id}, \"old\": {current_stock}, \"new\": {new_stock}}}"),
        )
    except Exception:
        # don't fail the operation if agent_logs is missing or insertion fails
        pass

    conn.commit()
    return {"product_name": name, "old_stock": current_stock, "new_stock": new_stock, "added": int(add_quantity)}


def simulate_demand_drop(conn, product_id, days=30, reduction_pct=70, protect_recent_days=7):
    """
    DEMO-ONLY TOOL: artificially lowers a product's recent demand signal,
    so you can trigger and show the Pricing Agent's "overstocked + falling
    demand" discount logic live.

    DESIGN HISTORY (kept here because the reasoning matters and isn't
    obvious): earlier versions tried (1) a flat uniform cut across all
    days, and (2) a linear ramp that crashed the MOST RECENT days hardest.
    Both were tested empirically and BOTH FAILED to produce a "falling"
    signal -- version (2) reliably produced the OPPOSITE result (demand
    looked like it was RISING).

    Why: the demand model's prediction is dominated by roll_mean_30 (a
    30-day average). The Pricing Agent compares that prediction against
    recent_7day_avg (a simple last-7-days average computed separately).
    If you crash the recent days hardest, recent_7day_avg craters while
    roll_mean_30 (mostly built from the untouched older days) stays
    relatively high -- so predicted demand ends up HIGHER than the
    crashed recent average, which reads as "recovering/rising", not
    falling.

    THE FIX: do the opposite. Leave the most recent `protect_recent_days`
    completely untouched (so recent_7day_avg stays at its real level),
    and crash the OLDER portion of the window instead. This pulls
    roll_mean_30 down while the recent comparison baseline stays where it
    was -- reliably producing predicted < recent_7day_avg, i.e. a real
    "falling demand" signal. Verified empirically across multiple
    products before shipping this version.

    This does NOT reflect real customer behavior -- it's purely to make
    the demo controllable. State this clearly if you use it in your viva.
    """
    cur = conn.cursor()

    # CRITICAL: anchor to the LATEST DATE ACTUALLY PRESENT in sales_history
    # for this product, NOT wall-clock "today" (datetime.now()). These can
    # drift apart significantly -- the database's data stops wherever
    # generate_data.py was last run, while real calendar time keeps moving
    # forward. The demand forecasting model (demand_utils.build_feature_row)
    # always looks at the most recent ROWS PRESENT in the table, not real
    # calendar dates -- so this simulation must anchor the same way, or it
    # can end up editing rows the model never even looks at.
    latest_date_row = cur.execute(
        "SELECT MAX(sale_date) FROM sales_history WHERE product_id = ?", (product_id,)
    ).fetchone()
    if latest_date_row is None or latest_date_row[0] is None:
        return {"rows_updated": 0, "reduction_pct": reduction_pct}
    latest_date = datetime.fromisoformat(latest_date_row[0]).date()
    cutoff_date = (latest_date - timedelta(days=days)).isoformat()

    rows = cur.execute(
        "SELECT sale_id, sale_date, units_sold FROM sales_history WHERE product_id = ? AND sale_date >= ? ORDER BY sale_date ASC",
        (product_id, cutoff_date),
    ).fetchall()

    total_rows = len(rows)
    if total_rows == 0:
        return {"rows_updated": 0, "reduction_pct": reduction_pct}

    protect_from_idx = max(0, total_rows - protect_recent_days)
    updated = 0
    for idx, (sale_id, sale_date, units_sold) in enumerate(rows):
        if idx < protect_from_idx:  # only touch the OLDER portion of the window
            reduced = max(0, int(units_sold * (1 - reduction_pct / 100)))
            cur.execute("UPDATE sales_history SET units_sold = ? WHERE sale_id = ?", (reduced, sale_id))
            updated += 1

    conn.commit()
    return {"rows_updated": updated, "reduction_pct": reduction_pct}