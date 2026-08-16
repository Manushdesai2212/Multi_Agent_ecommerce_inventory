"""
demand_utils.py
================
Shared helper functions for building feature rows to feed into the trained
demand forecasting model (demand_model.pkl). Used by both the Inventory
Monitor Agent and the Demand Forecasting Agent, so the feature-building
logic lives in exactly one place instead of being duplicated.
"""

import sqlite3
import pandas as pd
import numpy as np


def build_feature_row(conn, product_id, feature_cols, assume_promo=0):
    """
    Builds a single feature row representing "tomorrow" for a given product,
    using the same feature definitions used during model training (lag_1,
    lag_7, rolling averages, calendar features, price, category dummies).

    Returns a pandas DataFrame with exactly one row, ordered to match
    feature_cols, ready to be passed into model.predict().

    NOTE: this predicts ONE day ahead using a simplified approach (not a
    recursive multi-day forecast) -- for weekly estimates, the caller
    multiplies the single-day prediction by 7. This is a deliberate
    simplification, documented here and in the report, to avoid the added
    complexity/fragility of recursive forecasting for a project at this
    stage.
    """
    # Pull the product's most recent 30 days of actual sales history
    history = pd.read_sql(
        """SELECT sale_date, units_sold FROM sales_history
           WHERE product_id = ?
           ORDER BY sale_date DESC LIMIT 30""",
        conn, params=(product_id,),
    )

    if history.empty:
        return None  # no history yet for this product -- caller should handle this

    history = history.sort_values("sale_date").reset_index(drop=True)
    last_date = pd.to_datetime(history["sale_date"].iloc[-1])
    target_date = last_date + pd.Timedelta(days=1)  # the day we're predicting

    recent_values = history["units_sold"].values

    # Replicate the same lag/rolling logic used in training, but anchored
    # on "tomorrow" instead of a historical row
    lag_1 = recent_values[-1] if len(recent_values) >= 1 else np.nan
    lag_7 = recent_values[-7] if len(recent_values) >= 7 else np.nan
    roll_mean_7 = recent_values[-7:].mean() if len(recent_values) >= 7 else np.nan
    roll_mean_14 = recent_values[-14:].mean() if len(recent_values) >= 14 else np.nan
    roll_mean_30 = recent_values[-30:].mean() if len(recent_values) >= 30 else np.nan
    roll_std_7 = recent_values[-7:].std() if len(recent_values) >= 7 else np.nan
    trend_14 = roll_mean_7 - roll_mean_14 if len(recent_values) >= 14 else 0

    # Get product's category and price
    product_lookup = pd.read_sql(
        "SELECT category, price FROM products WHERE product_id = ?",
        conn, params=(product_id,),
    )
    if product_lookup.empty:
        # Product not found -- e.g. a stale/inconsistent DB state.
        # Skip gracefully instead of crashing the whole dashboard, same
        # as the "no sales history yet" case above.
        return None
    product_info = product_lookup.iloc[0]

    row = {
        "lag_1": lag_1,
        "lag_7": lag_7,
        "roll_mean_7": roll_mean_7,
        "roll_mean_14": roll_mean_14,
        "roll_mean_30": roll_mean_30,
        "roll_std_7": roll_std_7,
        "trend_14": trend_14,
        "day_of_week": target_date.dayofweek,
        "is_weekend": int(target_date.dayofweek >= 5),
        "month": target_date.month,
        "day_of_month": target_date.day,
        "price": product_info["price"],
        "is_promo": assume_promo,
    }

    # One-hot category columns -- must match exactly what the model saw
    # during training (e.g. category_Beauty, category_Electronics, ...)
    category = product_info["category"]
    for col in feature_cols:
        if col.startswith("category_"):
            cat_name = col.replace("category_", "")
            row[col] = 1 if category == cat_name else 0

    # Build the DataFrame in the EXACT column order the model expects
    row_df = pd.DataFrame([row])[feature_cols]
    return row_df


def predict_next_day_demand(conn, product_id, model, feature_cols, assume_promo=0):
    """
    Returns a single predicted units_sold value for the next day, or None
    if there isn't enough sales history yet for this product.
    """
    row_df = build_feature_row(conn, product_id, feature_cols, assume_promo)
    if row_df is None or row_df.isnull().values.any():
        return None  # not enough history to compute all required features yet
    prediction = model.predict(row_df)[0]
    return max(0, prediction)  # demand can't be negative