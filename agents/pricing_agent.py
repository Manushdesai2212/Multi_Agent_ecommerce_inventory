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
    Decide a suggested discount and urgency for a product given its
    days_of_stock and percent change in demand (predicted vs recent).

    New policy (per user request):
      - Any product with falling demand (demand_change_pct < 0) should
        appear in the Pricing suggestions list (even if not overstocked).
      - If a product is BOTH overstocked (many days_of_stock) AND has
        sustained low demand, mark it as higher urgency / larger discount.

    Returns (discount_pct, urgency_level). Returns (0, None) if demand
    is stable or rising.
    """
    if demand_change_pct >= 0:
        return 0, None  # only consider products with falling demand

    # Heavily stocked AND falling demand -> escalate urgency and size
    if days_of_stock >= 45:
        if demand_change_pct <= -30:
            return 25, "High"
        elif demand_change_pct <= -15:
            return 15, "High"
        else:
            return 10, "High"

    # Falling demand but not (yet) overstocked -> show advisory suggestion
    # so business owners are aware and can monitor or act early.
    if demand_change_pct <= -30:
        return 12, "Medium"
    elif demand_change_pct <= -15:
        return 8, "Low"
    else:
        return 5, "Low"


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

    # New behavior: consider ALL products with falling demand, not just
    # those currently flagged as overstocked. Inventory status still
    # influences the urgency/size of the suggested discount.
    for item in inventory_results:
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