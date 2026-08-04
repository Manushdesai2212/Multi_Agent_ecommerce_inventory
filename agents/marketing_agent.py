"""
Marketing & Reporting Agent
=============================
Communication-only agent (no ML model of its own) -- it takes findings
from the Inventory, Forecasting, and Pricing agents and turns them into:
  1. Draft promotional emails (restock urgency / clearance discount)
  2. A plain-language performance summary (revenue trend, category insight)

LLM INTEGRATION NOTE: call_llm() below tries to use Groq's API if a
GROQ_API_KEY environment variable is set. If it's not set (e.g. while
testing before you've set up an API key), it falls back to a simple
template so the agent is fully testable either way. Once you set
GROQ_API_KEY, the agent automatically starts using real LLM-generated
text -- no other code changes needed.

To set up Groq later: pip install groq, get a free API key from
console.groq.com, then run (Mac/Linux): export GROQ_API_KEY="your-key-here"
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta
import sys

sys.path.append(os.path.dirname(__file__))
from inventory_agent import check_inventory, load_model as load_demand_model
from pricing_agent import get_pricing_suggestions

DB_PATH = "data/ecommerce.db"


def call_llm(prompt, max_tokens=200):
    """
    Tries a real Groq LLM call if GROQ_API_KEY is set; otherwise returns
    None so the caller can fall back to a template. Kept isolated here so
    swapping/upgrading the LLM provider later only touches this function.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None

    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  [LLM call failed, falling back to template: {e}]")
        return None


def log_action(conn, action, details):
    conn.execute(
        "INSERT INTO agent_logs (timestamp, agent_name, action, details) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), "Marketing & Reporting", action, json.dumps(details)),
    )
    conn.commit()


# ---------------------------------------------------------------------
# Email drafting
# ---------------------------------------------------------------------

def draft_restock_email(product):
    prompt = (
        f"Write a short, friendly promotional email (2-3 sentences) for an e-commerce "
        f"store, telling customers that '{product['name']}' is running low on stock "
        f"and creating urgency to buy soon. Do not include a subject line, just the body."
    )
    llm_body = call_llm(prompt)

    subject = f"Almost gone: {product['name']} is running low!"
    body = llm_body or (
        f"Hi there,\n\n"
        f"Just a heads up -- our {product['name']} is running low on stock, "
        f"with only {product['current_stock']} units left. "
        f"If you've had your eye on it, now's a good time to grab it before it's gone.\n\n"
        f"Shop now while supplies last!"
    )
    return {"subject": subject, "body": body, "trigger": f"Low stock: {product['name']}", "product_id": product["product_id"]}


def draft_clearance_email(suggestion):
    prompt = (
        f"Write a short, friendly promotional email (2-3 sentences) for an e-commerce "
        f"store, announcing a {suggestion['suggested_discount_pct']}% discount on "
        f"'{suggestion['name']}' to encourage sales of overstocked inventory. "
        f"Do not include a subject line, just the body."
    )
    llm_body = call_llm(prompt)

    subject = f"{suggestion['suggested_discount_pct']}% off {suggestion['name']} -- limited time!"
    body = llm_body or (
        f"Hi there,\n\n"
        f"For a limited time, enjoy {suggestion['suggested_discount_pct']}% off "
        f"our {suggestion['name']}. It's a great chance to grab it at a special price "
        f"before this offer ends.\n\n"
        f"Shop the sale now!"
    )
    return {"subject": subject, "body": body, "trigger": f"Clearance: {suggestion['name']}", "product_id": suggestion["product_id"]}


def get_draft_emails(conn):
    model, feature_cols = load_demand_model()
    inventory_results = check_inventory(conn, model, feature_cols)
    pricing_suggestions = get_pricing_suggestions(conn)

    emails = []
    for item in inventory_results:
        if item["status"] == "low_stock":
            emails.append(draft_restock_email(item))

    for suggestion in pricing_suggestions:
        emails.append(draft_clearance_email(suggestion))

    return emails


# ---------------------------------------------------------------------
# Performance summary
# ---------------------------------------------------------------------

def compute_performance_stats(conn):
    """Pulls real numbers from orders/returns for the last 30 days vs the prior 30 days."""
    today = datetime.now().date()
    period_start = (today - timedelta(days=30)).isoformat()
    prior_start = (today - timedelta(days=60)).isoformat()

    current_revenue = conn.execute(
        "SELECT COALESCE(SUM(total_amount), 0) FROM orders WHERE order_date >= ?", (period_start,)
    ).fetchone()[0]

    prior_revenue = conn.execute(
        "SELECT COALESCE(SUM(total_amount), 0) FROM orders WHERE order_date >= ? AND order_date < ?",
        (prior_start, period_start),
    ).fetchone()[0]

    top_category = conn.execute(
        """SELECT p.category, SUM(o.total_amount) as rev
           FROM orders o JOIN products p ON o.product_id = p.product_id
           WHERE o.order_date >= ?
           GROUP BY p.category ORDER BY rev DESC LIMIT 1""",
        (period_start,),
    ).fetchone()

    return {
        "current_revenue": round(current_revenue, 2),
        "prior_revenue": round(prior_revenue, 2),
        "top_category": top_category[0] if top_category else "N/A",
        "top_category_revenue": round(top_category[1], 2) if top_category else 0,
    }


def generate_performance_summary(conn):
    stats = compute_performance_stats(conn)

    if stats["prior_revenue"] > 0:
        change_pct = ((stats["current_revenue"] - stats["prior_revenue"]) / stats["prior_revenue"]) * 100
    else:
        change_pct = 0

    prompt = (
        f"Write a 2-sentence plain-language business summary for a store owner in India. "
        f"Use the Indian Rupee symbol (\u20b9), not dollars. "
        f"Revenue in the last 30 days was \u20b9{stats['current_revenue']}, "
        f"a change of {change_pct:.1f}% vs the prior 30 days. "
        f"The top-performing category was {stats['top_category']} "
        f"with \u20b9{stats['top_category_revenue']} in revenue."
    )
    llm_summary = call_llm(prompt)

    template_summary = (
        f"Revenue over the last 30 days was \u20b9{stats['current_revenue']:,.2f}, "
        f"{'up' if change_pct >= 0 else 'down'} {abs(change_pct):.1f}% compared to the prior period. "
        f"{stats['top_category']} was the top-performing category, generating "
        f"\u20b9{stats['top_category_revenue']:,.2f} in revenue."
    )

    return llm_summary or template_summary, stats


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)

    print("Running Marketing & Reporting Agent...\n")

    emails = get_draft_emails(conn)
    print(f"--- Drafted {len(emails)} promotional emails ---\n")
    for e in emails[:5]:
        print(f"Subject: {e['subject']}")
        print(f"Trigger: {e['trigger']}")
        print(f"Body: {e['body']}")
        print()

    summary, stats = generate_performance_summary(conn)
    print("--- Performance Summary ---")
    print(summary)

    log_action(conn, "marketing_report_generated", {
        "emails_drafted": len(emails),
        "revenue_stats": stats,
    })

    conn.close()