"""
Inventory Monitor Agent
=========================
Checks every product's current stock against its predicted near-term
demand (from the trained Demand Forecasting model) and flags:
  - "low_stock"    : will likely run out soon, needs reordering
  - "overstocked"  : sitting on far more stock than demand justifies
  - "healthy"      : no action needed

This agent is RULE-BASED on top of the ML model's output -- it does not
itself use a machine learning model, it consumes one (demand_model.pkl).
This is a deliberate design choice: not every agent needs its own model,
some just need to reason over another model's predictions.

Every decision is logged to agent_logs so the reasoning is auditable --
nothing here happens silently.
"""

import sqlite3
import pandas as pd
import joblib
import json
from datetime import datetime
import sys
import os

sys.path.append(os.path.dirname(__file__))
from demand_utils import predict_next_day_demand

DB_PATH = "data/ecommerce.db"
MODEL_PATH = "models/demand_model.pkl"
FEATURES_PATH = "models/demand_model_features.txt"

# Thresholds -- tunable, kept simple and explainable on purpose
LOW_STOCK_DAYS_THRESHOLD = 5      # flag if predicted days-until-stockout < this
OVERSTOCK_DAYS_THRESHOLD = 45     # flag if stock covers more than this many days of demand


def load_model():
    model = joblib.load(MODEL_PATH)
    with open(FEATURES_PATH) as f:
        feature_cols = f.read().splitlines()
    return model, feature_cols


def log_action(conn, action, details):
    conn.execute(
        "INSERT INTO agent_logs (timestamp, agent_name, action, details) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), "Inventory Monitor", action, json.dumps(details)),
    )
    conn.commit()


def check_inventory(conn, model, feature_cols):
    """
    Runs the check across every product and returns a list of result dicts.
    This is the function the Streamlit dashboard will later call directly.
    """
    products = pd.read_sql("SELECT product_id, name, category, stock_qty, reorder_level FROM products", conn)
    results = []

    for _, product in products.iterrows():
        pid = int(product["product_id"])
        predicted_daily_demand = predict_next_day_demand(conn, pid, model, feature_cols)

        if predicted_daily_demand is None:
            # Not enough sales history yet for this product -- skip, don't guess
            continue

        current_stock = int(product["stock_qty"])
        reorder_level = int(product["reorder_level"])

        # Avoid divide-by-zero if predicted demand rounds to ~0
        safe_demand = max(predicted_daily_demand, 0.1)
        days_until_stockout = current_stock / safe_demand

        if current_stock <= reorder_level or days_until_stockout < LOW_STOCK_DAYS_THRESHOLD:
            status = "low_stock"
            # Recommend enough stock to cover ~30 days at predicted demand
            recommended_reorder_qty = max(0, round(predicted_daily_demand * 30 - current_stock))
        elif days_until_stockout > OVERSTOCK_DAYS_THRESHOLD:
            status = "overstocked"
            recommended_reorder_qty = 0
        else:
            status = "healthy"
            recommended_reorder_qty = 0

        result = {
            "product_id": pid,
            "name": product["name"],
            "category": product["category"],
            "current_stock": current_stock,
            "reorder_level": reorder_level,
            "predicted_daily_demand": round(predicted_daily_demand, 1),
            "days_until_stockout": round(days_until_stockout, 1),
            "status": status,
            "recommended_reorder_qty": recommended_reorder_qty,
        }
        results.append(result)

    return results


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    model, feature_cols = load_model()

    print("Running Inventory Monitor Agent...")
    results = check_inventory(conn, model, feature_cols)

    low_stock = [r for r in results if r["status"] == "low_stock"]
    overstocked = [r for r in results if r["status"] == "overstocked"]
    healthy = [r for r in results if r["status"] == "healthy"]

    print(f"\nChecked {len(results)} products with sufficient history")
    print(f"  Low stock:   {len(low_stock)}")
    print(f"  Overstocked: {len(overstocked)}")
    print(f"  Healthy:     {len(healthy)}")

    print("\n--- LOW STOCK products (need attention) ---")
    for r in low_stock[:10]:
        print(f"  [{r['product_id']}] {r['name']} ({r['category']}): "
              f"stock={r['current_stock']}, days_left={r['days_until_stockout']}, "
              f"reorder_qty={r['recommended_reorder_qty']}")

    print("\n--- OVERSTOCKED products (candidates for discount) ---")
    for r in overstocked[:10]:
        print(f"  [{r['product_id']}] {r['name']} ({r['category']}): "
              f"stock={r['current_stock']}, days_of_stock={r['days_until_stockout']}")

    # Log a summary of this run
    log_action(conn, "inventory_check_completed", {
        "total_checked": len(results),
        "low_stock_count": len(low_stock),
        "overstocked_count": len(overstocked),
    })

    conn.close()