"""
Returns & Refunds Agent
=========================
Reviews return requests using the trained return-risk classification model,
but DELIBERATELY NEVER makes the final decision -- it surfaces a risk
assessment with clear reasoning, and the business owner approves or
rejects. This design was chosen specifically to avoid unfairly penalizing
genuine customers who happen to match a surface-level risk pattern (e.g.
several legitimate returns due to bad luck, not abuse).

Two layers of logic, kept deliberately separate and explainable:
  1. Policy check: simple, transparent business rules (e.g. return window)
  2. ML risk score: the trained classifier's probability estimate

Both are shown to the owner side by side -- neither one alone decides
anything.
"""

import sqlite3
import pandas as pd
import joblib
import json
from datetime import datetime
import sys
import os

sys.path.append(os.path.dirname(__file__))

DB_PATH = "data/ecommerce.db"
MODEL_PATH = "models/returns_model.pkl"
FEATURES_PATH = "models/returns_model_features.txt"

# Policy is configurable PER CATEGORY, not one global number -- as decided
# earlier, since a fixed "30 days for everything" is unrealistic.
CATEGORY_RETURN_WINDOW_DAYS = {
    "Electronics":    7,    # standard short window -- matches typical Indian e-commerce platforms
    "Apparel":        15,   # longer window -- sizing/fit issues are common and legitimate
    "Home & Kitchen":  7,
    "Beauty":         7,    # short window -- hygiene/opened-product concerns
    "Sports":         7,
}
DEFAULT_RETURN_WINDOW_DAYS = 7


def load_model():
    model = joblib.load(MODEL_PATH)
    with open(FEATURES_PATH) as f:
        feature_cols = f.read().splitlines()
    return model, feature_cols


def log_action(conn, action, details):
    conn.execute(
        "INSERT INTO agent_logs (timestamp, agent_name, action, details) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), "Returns & Refunds", action, json.dumps(details)),
    )
    conn.commit()


def check_policy(days_since_order, category):
    """
    Simple, transparent rule -- not a verdict, just one input the owner sees.
    Returns (within_policy: bool, window_days: int)
    """
    window = CATEGORY_RETURN_WINDOW_DAYS.get(category, DEFAULT_RETURN_WINDOW_DAYS)
    return days_since_order <= window, window


def get_risk_level(risk_probability):
    """Converts a raw probability into a human-readable level for the dashboard badge."""
    if risk_probability >= 0.6:
        return "High"
    elif risk_probability >= 0.35:
        return "Medium"
    else:
        return "Low"


def build_reasoning(row, within_policy, window_days, risk_level, risk_probability):
    """
    Plain-language explanation shown when the owner clicks the risk badge --
    this is the transparency requirement: never a black-box label alone.
    """
    reasons = []

    if not within_policy:
        reasons.append(f"Return requested {row['days_since_order']} days after purchase, outside the {window_days}-day policy window for this category.")
    else:
        reasons.append(f"Return requested within the {window_days}-day policy window ({row['days_since_order']} days since order).")

    if row["customer_total_returns"] >= 4:
        reasons.append(f"This customer has made {row['customer_total_returns']} returns in total.")
    elif row["customer_total_returns"] >= 2:
        reasons.append(f"This customer has made {row['customer_total_returns']} returns previously.")

    if row["is_repeat_offender"] == 1:
        reasons.append("This customer was flagged as a frequent returner during account history review.")

    reasons.append(f"Model-estimated risk score: {risk_probability:.0%} ({risk_level}).")

    return " ".join(reasons)


def review_pending_returns(conn):
    """
    Returns a list of dicts for every return in the returns table that
    doesn't yet have an agent_decision recorded -- this is what the
    Returns dashboard page will display for owner review.
    """
    model, feature_cols = load_model()

    pending = pd.read_sql(
        """SELECT r.*, o.total_amount, p.category, c.is_repeat_offender
           FROM returns r
           JOIN orders o ON r.order_id = o.order_id
           JOIN products p ON r.product_id = p.product_id
           JOIN customers c ON r.customer_id = c.customer_id
           WHERE r.agent_decision IS NULL""",
        conn,
    )

    if pending.empty:
        return []

    # Build feature matrix matching training format exactly
    features_df = pd.get_dummies(pending, columns=["category"])
    for col in feature_cols:
        if col.startswith("category_") and col not in features_df.columns:
            features_df[col] = 0  # category not present in this batch -> 0, matches training format

    X = features_df[feature_cols]
    risk_probabilities = model.predict_proba(X)[:, 1]  # probability of class "flagged"

    results = []
    for i, row in pending.iterrows():
        within_policy, window_days = check_policy(row["days_since_order"], row["category"])
        risk_probability = risk_probabilities[i]
        risk_level = get_risk_level(risk_probability)
        reasoning = build_reasoning(row, within_policy, window_days, risk_level, risk_probability)

        results.append({
            "return_id": int(row["return_id"]),
            "customer_id": int(row["customer_id"]),
            "product_id": int(row["product_id"]),
            "category": row["category"],
            "reason": row["reason"],
            "days_since_order": int(row["days_since_order"]),
            "customer_total_returns": int(row["customer_total_returns"]),
            "within_policy": within_policy,
            "risk_level": risk_level,
            "risk_probability": round(float(risk_probability), 2),
            "reasoning": reasoning,
            # NOTE: no "decision" field here -- this agent never decides.
            # The dashboard shows Approve / Reject / Request More Info
            # buttons, and the owner's choice gets written back via
            # record_owner_decision() below.
        })

    return results


def record_owner_decision(conn, return_id, decision):
    """
    Called by the dashboard when the owner clicks Approve/Reject.
    This is the ONLY place agent_decision gets written -- always a human
    action, never set automatically by this agent.
    """
    if decision not in ("approved", "rejected", "more_info_requested"):
        raise ValueError(f"Invalid decision: {decision}")

    conn.execute(
        "UPDATE returns SET agent_decision = ? WHERE return_id = ?",
        (decision, return_id),
    )
    conn.commit()
    log_action(conn, "owner_decision_recorded", {"return_id": return_id, "decision": decision})


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)

    print("Running Returns & Refunds Agent (advisory review only)...\n")
    reviews = review_pending_returns(conn)

    print(f"Found {len(reviews)} pending returns to review\n")

    high_risk = [r for r in reviews if r["risk_level"] == "High"]
    medium_risk = [r for r in reviews if r["risk_level"] == "Medium"]
    low_risk = [r for r in reviews if r["risk_level"] == "Low"]

    print(f"  High risk:   {len(high_risk)}")
    print(f"  Medium risk: {len(medium_risk)}")
    print(f"  Low risk:    {len(low_risk)}")

    print("\n--- Sample HIGH risk returns (owner review needed) ---")
    for r in high_risk[:5]:
        print(f"\n  Return #{r['return_id']} -- {r['category']}, reason: {r['reason']}")
        print(f"  {r['reasoning']}")

    print("\n\n--- Sample LOW risk returns (likely straightforward approval) ---")
    for r in low_risk[:3]:
        print(f"\n  Return #{r['return_id']} -- {r['category']}, reason: {r['reason']}")
        print(f"  {r['reasoning']}")

    log_action(conn, "returns_review_completed", {
        "total_reviewed": len(reviews),
        "high_risk_count": len(high_risk),
        "medium_risk_count": len(medium_risk),
        "low_risk_count": len(low_risk),
    })

    conn.close()