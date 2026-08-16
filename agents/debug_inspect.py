import sqlite3
import sys
import os

sys.path.append(os.path.dirname(__file__))

from inventory_agent import load_model, check_inventory
from forecasting_agent import load_model as load_forecast_model, forecast_all_products

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'ecommerce.db')


def inspect_product(product_name):
    conn = sqlite3.connect(DB_PATH)
    # load models
    model, feature_cols = load_model()
    inv_results = check_inventory(conn, model, feature_cols)

    # find product id in inventory_results
    match = None
    for r in inv_results:
        if r['name'] == product_name:
            match = r
            break

    if not match:
        print(f"Product '{product_name}' not found or insufficient history for inventory check.")
        return

    # load forecast results
    fmodel, fcols = load_forecast_model()
    forecasts = forecast_all_products(conn, fmodel, fcols)
    forecast_by_id = {f['product_id']: f for f in forecasts}

    pid = match['product_id']
    f = forecast_by_id.get(pid)

    print("Product:", match['name'])
    print("Product ID:", pid)
    print("Current stock:", match['current_stock'])
    print("Reorder level:", match['reorder_level'])
    print("Status:", match['status'])
    print("Days until stockout:", match['days_until_stockout'])
    print("Predicted daily demand (inventory):", match['predicted_daily_demand'])
    if f:
        print("Predicted daily demand (forecast_agent):", f['predicted_daily_demand'])
        print("Recent 7-day avg:", f['recent_7day_avg'])
        print("Forecast summary:", f['summary'])
    else:
        print("No forecast available (insufficient data?)")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python agents/debug_inspect.py 'Product Name'")
        sys.exit(1)
    pname = sys.argv[1]
    inspect_product(pname)
