"""
Pricing / Discount Optimization Agent
========================================
Combines the Inventory Monitor's "overstocked" flag with the Demand
Forecasting Agent's trend signal to suggest a discount percentage for
products that are both overstocked AND seeing falling/weak demand.

This is a DECISION-MAKING agent (unlike Marketing & Reporting, which is
communication-only) -- it computes an actual number (a suggested discount
%) using explicit business rules. This is why it's kept as its own agent
rather than merged into Marketing: the reasoning behind "what discount to
suggest" is a distinct, explainable piece of logic worth isolating.

The suggested discount is ADVISORY -- it is shown to the business owner
for approval/edit on the Pricing & Discounts dashboard page, never applied
automatically.
"""

import sqlite3
import json
from datetime import datetime
import sys
import os

sys.path.append(os.path.dirname(__file__))
from inventory_agent import check_inventory, load_model as load_demand_model
from forecasting_agent import forecast_all_products

DB_PATH = "data/ecommerce.db"

# Simple, explainable discount rule tiers based on how overstocked +
# how much demand is falling. Kept as a clear rule table rather than
# a black-box formula, so it's easy to explain and adjust in a viva.
def compute_discount(days_of_stock, demand_change_pct):
    """
    days_of_stock: how many days current stock will last at predicted demand
    demand_change_pct: % change vs recent average (negative = falling)

    Returns (discount_pct, urgency_level). Returns (0, None) if no discount
    is warranted.

    IMPORTANT: a discount is only suggested when stock is high AND demand
    is genuinely falling. High stock with STABLE or RISING demand is not
    flagged here -- that inventory will naturally sell through as demand
    catches up, so a discount would be an unnecessary markdown. This was
    a real bug in the earlier version, which suggested discounts even for
    overstocked products with rising demand.
    """
    if days_of_stock < 45:
        return 0, None  # not overstocked enough to warrant a discount

    if demand_change_pct >= 0:
        return 0, None  # demand stable or rising -- overstock will likely resolve naturally

    # From here, stock is high AND demand is genuinely falling -- a real problem
    if demand_change_pct <= -30:
        return 20, "High"
    elif demand_change_pct <= -15:
        return 12, "Medium"
    else:
        return 8, "Low"


def log_action(conn, action, details):
    conn.execute(
        "INSERT INTO agent_logs (timestamp, agent_name, action, details) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), "Pricing & Discount", action, json.dumps(details)),
    )
    conn.commit()


def get_pricing_suggestions(conn):
    """
    Cross-references Inventory Monitor's overstocked list with Demand
    Forecasting's trend data to produce discount suggestions.
    Returns a list of dicts -- what the Pricing & Discounts dashboard
    page will display.
    """
    model, feature_cols = load_demand_model()

    inventory_results = check_inventory(conn, model, feature_cols)
    forecast_results = forecast_all_products(conn, model, feature_cols)

    # index forecasts by product_id for quick lookup
    forecast_by_id = {r["product_id"]: r for r in forecast_results}

    suggestions = []
    overstocked = [r for r in inventory_results if r["status"] == "overstocked"]

    for item in overstocked:
        pid = item["product_id"]
        forecast = forecast_by_id.get(pid)
        if forecast is None or forecast["recent_7day_avg"] in (None, 0):
            continue

        demand_change_pct = (
            (forecast["predicted_daily_demand"] - forecast["recent_7day_avg"])
            / forecast["recent_7day_avg"] * 100
        )

        discount, urgency = compute_discount(item["days_until_stockout"], demand_change_pct)

        if discount > 0:
            suggestions.append({
                "product_id": pid,
                "name": item["name"],
                "category": item["category"],
                "current_stock": item["current_stock"],
                "days_of_stock": item["days_until_stockout"],
                "demand_trend_pct": round(demand_change_pct, 1),
                "suggested_discount_pct": discount,
                "urgency": urgency,
            })

    return suggestions


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)

    print("Running Pricing & Discount Optimization Agent...\n")
    suggestions = get_pricing_suggestions(conn)

    print(f"Found {len(suggestions)} products warranting a discount suggestion:\n")
    for s in suggestions:
        print(f"  [{s['product_id']}] {s['name']} ({s['category']}): "
              f"stock lasts {s['days_of_stock']} days, "
              f"demand trend {s['demand_trend_pct']}%, "
              f"-> suggest {s['suggested_discount_pct']}% discount")

    log_action(conn, "pricing_suggestions_generated", {
        "total_suggestions": len(suggestions),
    })

    conn.close()