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
from forecasting_agent import forecast_all_products

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
# Promotion ideas (BOGO, free gift, bundle suggestions)
# ---------------------------------------------------------------------


def find_top_selling_in_category(conn, category, exclude_product_id=None):
    """Return a product_id and name for a top-selling product in the same category.
    Uses recent orders to pick a sensible free-gift or bundle partner.
    """
    q = (
        "SELECT p.product_id, p.name, COALESCE(SUM(o.quantity),0) as sold "
        "FROM products p LEFT JOIN orders o ON p.product_id = o.product_id "
        "WHERE p.category = ?"
    )
    params = [category]
    if exclude_product_id is not None:
        q += " AND p.product_id != ?"
        params.append(exclude_product_id)
    q += " GROUP BY p.product_id ORDER BY sold DESC LIMIT 1"

    row = conn.execute(q, params).fetchone()
    if row:
        return {"product_id": row[0], "name": row[1]}
    return None


def generate_promo_ideas(conn, max_ideas=10):
    """Generate simple, rule-based promotional ideas for marketing.

    Returns a list of suggestion dicts with keys: `type`, `title`, `description`,
    and `products` (list of involved product ids/names).
    """
    model, feature_cols = load_demand_model()
    inventory_results = check_inventory(conn, model, feature_cols)
    pricing_suggestions = get_pricing_suggestions(conn)
    forecasts = forecast_all_products(conn, model, feature_cols)
    forecast_by_id = {f["product_id"]: f for f in forecasts}

    ideas = []

    # 1) For pricing suggestions (overstocked + falling demand) propose BOGO or bundle
    for s in pricing_suggestions:
        if len(ideas) >= max_ideas:
            break
        pid = s["product_id"]
        days = float(s.get("days_of_stock", 0))
        # pick partner product from same category if available
        partner = find_top_selling_in_category(conn, s["category"], exclude_product_id=pid)

        if days >= 45:
            # heavy stock -> strong action: BOGO or free accessory
            if partner:
                title = f"Bundle: Buy 1 {s['name']} + get {partner['name']} free"
                desc = (
                    f"Move excess {s['name']} by bundling it with popular {partner['name']} in {s['category']}. "
                    f"Offer the partner item free or at a heavy discount when customers buy {s['name']}."
                )
                products = [ {"product_id": pid, "name": s['name']}, partner ]
            else:
                title = f"BOGO: Buy one get one free on {s['name']}"
                desc = (
                    f"Offer a BOGO promotion on {s['name']} to quickly reduce large stock levels. "
                    f"This is recommended because stock lasts ~{days} days and demand is falling ({s['demand_trend_pct']}%)."
                )
                products = [{"product_id": pid, "name": s['name']}]

            ideas.append({"type": "bundle", "title": title, "description": desc, "products": products, "urgency": s.get("urgency")})
        else:
            # moderate stock -> light promotion or free gift
            title = f"Offer: Free gift with purchase of {s['name']}"
            desc = (
                f"Encourage purchases of {s['name']} by including a small free gift or trial-sized item. "
                f"Useful when demand is falling but stock isn't extremely high.")
            products = [{"product_id": pid, "name": s['name']}]
            ideas.append({"type": "free_gift", "title": title, "description": desc, "products": products, "urgency": s.get("urgency")})

    # 2) For products with falling demand but not in pricing suggestions (caught earlier), propose light promos
    for item in inventory_results:
        if len(ideas) >= max_ideas:
            break
        pid = item["product_id"]
        f = forecast_by_id.get(pid)
        if not f:
            continue
        demand_change_pct = ((f["predicted_daily_demand"] - f["recent_7day_avg"]) / f["recent_7day_avg"]) * 100 if f["recent_7day_avg"] not in (None, 0) else 0
        if demand_change_pct < 0 and not any(i for i in ideas if any(p["product_id"]==pid for p in i.get("products",[]))):
            title = f"Email idea: Highlight {item['name']} in a targeted campaign"
            desc = (
                f"Draft a targeted email or social post highlighting {item['name']} with a small incentive (free shipping, 5% off) to reignite interest."
            )
            ideas.append({"type": "email_prompt", "title": title, "description": desc, "products": [{"product_id": pid, "name": item['name']}], "urgency": item.get("status")})

    return ideas


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