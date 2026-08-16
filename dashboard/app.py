"""
Streamlit Dashboard
=====================
Ties all 5 agents into one clickable interface for the business owner.
Five pages: Home/Summary, Inventory & Forecast, Pricing & Discounts,
Marketing & Reporting, Returns.

Run with: streamlit run dashboard/app.py
(run this command from the multi-agent/ project root, not from inside
dashboard/, so the relative paths to data/ and models/ resolve correctly)
"""

import streamlit as st
import sqlite3
import pandas as pd
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "agents"))

from inventory_agent import check_inventory, load_model as load_demand_model
from forecasting_agent import forecast_all_products
from pricing_agent import get_pricing_suggestions
from marketing_agent import get_draft_emails, generate_performance_summary, generate_promo_ideas
from returns_agent import review_pending_returns, record_owner_decision

# Import simulate module and explicitly reload it to ensure Streamlit's
# autoreload/re-run behavior picks up recent edits (prevents stale imports)
import importlib
import simulate as simulate_mod
importlib.reload(simulate_mod)
from simulate import simulate_order, simulate_demand_drop, simulate_return, simulate_increase_stock

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ecommerce.db")

st.set_page_config(page_title="E-commerce AI Operations", layout="wide")


def get_connection():
    """
    Deliberately NOT cached with @st.cache_resource. A cached connection
    can go stale if the underlying ecommerce.db file gets deleted and
    rebuilt (e.g. re-running generate_data.py) while the dashboard is
    still running -- this caused a real crash (IndexError: product not
    found) because the app kept using a connection pointing at an
    inconsistent mid-rebuild state of the file. SQLite connection
    overhead at this project's scale is negligible, so opening fresh
    each rerun is simply safer.
    """
    return sqlite3.connect(DB_PATH, check_same_thread=False)


# Cache agent results for a short time so switching between pages doesn't
# re-run every model prediction from scratch on every click -- but the
# cache clears whenever the user hits the manual refresh button.
@st.cache_data(ttl=300)
def cached_inventory(_conn):
    model, feature_cols = load_demand_model()
    return check_inventory(_conn, model, feature_cols)


@st.cache_data(ttl=300)
def cached_forecast(_conn):
    model, feature_cols = load_demand_model()
    return forecast_all_products(_conn, model, feature_cols)


@st.cache_data(ttl=300)
def cached_pricing(_conn):
    return get_pricing_suggestions(_conn)


@st.cache_data(ttl=300)
def cached_emails(_conn):
    return get_draft_emails(_conn)


@st.cache_data(ttl=300)
def cached_summary(_conn):
    return generate_performance_summary(_conn)


@st.cache_data(ttl=60)
def cached_returns(_conn):
    return review_pending_returns(_conn)


conn = get_connection()

st.sidebar.title("E-commerce AI Ops")
page = st.sidebar.radio(
    "Navigate",
    ["Home / Summary", "Inventory & Forecast", "Pricing & Discounts", "Marketing & Reporting", "Returns", "Simulate Activity"],
)

if st.sidebar.button("Refresh all data"):
    st.cache_data.clear()
    st.rerun()


# ---------------------------------------------------------------------
# PAGE 1: Home / Summary
# ---------------------------------------------------------------------
if page == "Home / Summary":
    st.title("Business overview")

    inventory_results = cached_inventory(conn)
    pricing_suggestions = cached_pricing(conn)
    returns_results = cached_returns(conn)
    summary_text, stats = cached_summary(conn)

    low_stock_count = len([r for r in inventory_results if r["status"] == "low_stock"])
    pending_returns_count = len(returns_results)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total products checked", len(inventory_results))
    col2.metric("Low stock alerts", low_stock_count)
    col3.metric("Pending returns", pending_returns_count)
    col4.metric("Revenue (last 30 days)", f"\u20b9{stats['current_revenue']:,.0f}")

    st.subheader("Performance summary")
    st.write(summary_text.replace("$", "\\$"))

    st.subheader("Needs your attention")
    attention_items = []
    for r in inventory_results:
        if r["status"] == "low_stock":
            attention_items.append(f"**{r['name']}** -- only {r['current_stock']} units left, ~{r['days_until_stockout']} days of stock remaining")
    for s in pricing_suggestions[:3]:
        attention_items.append(f"**{s['name']}** is overstocked -- {s['suggested_discount_pct']}% discount suggested")
    high_risk_returns = [r for r in returns_results if r["risk_level"] == "High"]
    if high_risk_returns:
        attention_items.append(f"{len(high_risk_returns)} return(s) flagged as high risk, awaiting your review")

    if attention_items:
        for item in attention_items[:8]:
            st.markdown(f"- {item}")
    else:
        st.write("Nothing urgent right now.")


# ---------------------------------------------------------------------
# PAGE 2: Inventory & Forecast
# ---------------------------------------------------------------------
elif page == "Inventory & Forecast":
    st.title("Inventory & demand forecast")

    inventory_results = cached_inventory(conn)
    forecast_results = cached_forecast(conn)
    forecast_by_id = {r["product_id"]: r for r in forecast_results}

    df = pd.DataFrame(inventory_results)

    status_filter = st.multiselect(
        "Filter by status", options=["low_stock", "overstocked", "healthy"],
        default=["low_stock", "overstocked", "healthy"],
    )
    df_filtered = df[df["status"].isin(status_filter)].sort_values(
        by="status", key=lambda s: s.map({"low_stock": 0, "overstocked": 1, "healthy": 2})
    )

    st.dataframe(
        df_filtered[["name", "category", "current_stock", "reorder_level",
                      "predicted_daily_demand", "days_until_stockout", "status"]],
        width="stretch",
        hide_index=True,
    )

    st.subheader("Product detail")
    product_names = df["name"].tolist()
    selected_name = st.selectbox("Select a product to see its forecast", product_names)

    selected_row = df[df["name"] == selected_name].iloc[0]
    pid = int(selected_row["product_id"])
    forecast = forecast_by_id.get(pid)

    if forecast:
        st.write(forecast["summary"])

        history = pd.read_sql(
            "SELECT sale_date, units_sold FROM sales_history WHERE product_id = ? ORDER BY sale_date DESC LIMIT 30",
            conn, params=(pid,),
        ).sort_values("sale_date")
        st.line_chart(history.set_index("sale_date")["units_sold"])


# ---------------------------------------------------------------------
# PAGE 3: Pricing & Discounts
# ---------------------------------------------------------------------
elif page == "Pricing & Discounts":
    st.title("Pricing & discount suggestions")
    st.caption("Only products that are both overstocked AND seeing falling demand get a discount suggestion -- rising or stable demand means the stock will likely sell through naturally.")

    suggestions = cached_pricing(conn)
    inventory_results = cached_inventory(conn)
    forecast_results = cached_forecast(conn)
    forecast_by_id = {r["product_id"]: r for r in forecast_results}

    if not suggestions:
        st.info("No discount suggestions right now -- no overstocked products are currently seeing falling demand.")
    else:
        urgency_filter = st.multiselect(
            "Filter by urgency", options=["High", "Medium", "Low"], default=["High", "Medium", "Low"]
        )
        filtered = [s for s in suggestions if s["urgency"] in urgency_filter]

        urgency_badge = {"High": "red", "Medium": "orange", "Low": "gray"}

        for s in filtered:
            with st.container(border=True):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.markdown(f"**{s['name']}** ({s['category']})  :{urgency_badge[s['urgency']]}[{s['urgency']} urgency]")
                    st.caption(
                        f"Stock lasts ~{s['days_of_stock']} days | "
                        f"Demand falling {s['demand_trend_pct']}% | "
                        f"Suggested discount: {s['suggested_discount_pct']}%"
                    )
                with col2:
                    st.button("Approve", key=f"approve_{s['product_id']}")
                    st.button("Skip", key=f"skip_{s['product_id']}")

    # Informational section: overstocked products that DON'T need a discount
    # (demand is stable/rising) -- shown so the page explains the "no
    # suggestions" state instead of looking broken or empty.
    overstocked = [r for r in inventory_results if r["status"] == "overstocked"]
    if overstocked:
        st.divider()
        st.subheader("Overstocked, but no action needed")
        st.caption("These products have high stock, but demand is stable or rising -- likely to sell through naturally without a discount.")

        rows = []
        for item in overstocked:
            f = forecast_by_id.get(item["product_id"])
            trend_pct = None
            if f and f["recent_7day_avg"]:
                trend_pct = round((f["predicted_daily_demand"] - f["recent_7day_avg"]) / f["recent_7day_avg"] * 100, 1)
            rows.append({
                "Product": item["name"],
                "Category": item["category"],
                "Stock lasts (days)": item["days_until_stockout"],
                "Demand trend": f"+{trend_pct}%" if trend_pct and trend_pct >= 0 else f"{trend_pct}%" if trend_pct is not None else "N/A",
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# ---------------------------------------------------------------------
# PAGE 4: Marketing & Reporting
# ---------------------------------------------------------------------
elif page == "Marketing & Reporting":
    st.title("Marketing & reporting")

    tab1, tab2 = st.tabs(["Draft emails", "Performance summary"])

    with tab1:
        emails = cached_emails(conn)
        st.write(f"{len(emails)} draft emails ready for review")

        for i, e in enumerate(emails):
            with st.expander(f"{e['subject']} -- ({e['trigger']})"):
                edited_body = st.text_area("Email body", value=e["body"], key=f"email_{i}", height=120)
                col1, col2 = st.columns(2)
                col1.button("Send", key=f"send_{i}")
                col2.button("Discard", key=f"discard_{i}")

        st.divider()
        st.subheader("Promotional ideas")
        st.caption("Automated campaign ideas (BOGO, bundle with free gift, targeted email prompts) based on inventory and demand signals.")
        ideas = generate_promo_ideas(conn)
        if not ideas:
            st.write("No promo ideas right now.")
        else:
            for idx, idea in enumerate(ideas):
                with st.container(border=True):
                    st.markdown(f"**{idea['title']}**")
                    st.write(idea['description'])
                    prod_names = ", ".join([p['name'] for p in idea.get('products',[])])
                    st.caption(f"Products: {prod_names} | Urgency: {idea.get('urgency')}")

    with tab2:
        summary_text, stats = cached_summary(conn)
        st.write(summary_text.replace("$", "\\$"))

        col1, col2 = st.columns(2)
        col1.metric("Current 30-day revenue", f"\u20b9{stats['current_revenue']:,.2f}")
        col2.metric("Prior 30-day revenue", f"\u20b9{stats['prior_revenue']:,.2f}")
        st.write(f"Top category: **{stats['top_category']}** (\u20b9{stats['top_category_revenue']:,.2f})")


# ---------------------------------------------------------------------
# PAGE 5: Returns
# ---------------------------------------------------------------------
elif page == "Returns":
    st.title("Returns review")
    st.caption("Risk scores are advisory only -- you make the final call on every return.")

    returns_results = cached_returns(conn)

    if not returns_results:
        st.write("No pending returns to review.")
    else:
        risk_filter = st.multiselect(
            "Filter by risk level", options=["High", "Medium", "Low"], default=["High", "Medium", "Low"]
        )
        filtered = [r for r in returns_results if r["risk_level"] in risk_filter]

        for r in filtered:
            with st.container(border=True):
                badge_color = {"High": "red", "Medium": "orange", "Low": "green"}[r["risk_level"]]
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.markdown(f"**Return #{r['return_id']}** -- {r['category']}, reason: {r['reason']}")
                    badges = f":{badge_color}[{r['risk_level']} risk] ({r['risk_probability']:.0%})"
                    if not r["within_policy"]:
                        badges += "  &nbsp;&nbsp; :orange[**\u26a0\ufe0f Policy Exception**]"
                    st.markdown(badges)
                    if not r["within_policy"]:
                        st.caption("This return is outside the standard policy window. Approving it means consciously granting an exception, not a routine approval.")
                    with st.expander("Why this risk level?"):
                        st.write(r["reasoning"])
                with col2:
                    if st.button("Approve", key=f"ret_approve_{r['return_id']}"):
                        record_owner_decision(conn, r["return_id"], "approved")
                        st.cache_data.clear()
                        st.rerun()
                    if st.button("Reject", key=f"ret_reject_{r['return_id']}"):
                        record_owner_decision(conn, r["return_id"], "rejected")
                        st.cache_data.clear()
                        st.rerun()


# ---------------------------------------------------------------------
# PAGE 6: Simulate Activity (testing/demo tool, not a real business page)
# ---------------------------------------------------------------------
elif page == "Simulate Activity":
    st.title("Simulate live activity")
    st.warning(
        "This page exists ONLY for testing and demo purposes -- it lets you simulate real "
        "customer activity (orders happening, demand shifting) since this project has no live "
        "e-commerce website sending real orders. In production, a real store's website would "
        "update this database automatically, and every agent would react the same way."
    )

    products_df = pd.read_sql("SELECT product_id, name, stock_qty, price FROM products ORDER BY name", conn)

    st.subheader("Simulate a customer order")
    st.caption("Reduces stock, adds a real order, and adds to today's sales history -- then re-check the Inventory or Pricing page to see agents react.")

    col1, col2 = st.columns([3, 1])
    with col1:
        selected_product = st.selectbox(
            "Product", products_df["name"],
            format_func=lambda name: f"{name} (stock: {products_df[products_df['name']==name]['stock_qty'].values[0]})",
        )
    with col2:
        qty = st.number_input("Quantity", min_value=1, max_value=500, value=10)

    if st.button("Place simulated order"):
        pid = int(products_df[products_df["name"] == selected_product]["product_id"].values[0])
        try:
            result = simulate_order(conn, pid, qty)
            st.success(
                f"Order placed: {result['quantity']} units of {result['product_name']}. "
                f"Stock: {result['old_stock']} -> {result['new_stock']}. "
                f"Order value: \u20b9{result['total_amount']:,.2f}"
            )
            st.cache_data.clear()
            st.info("Now go to Inventory & Forecast or Pricing & Discounts to see if the agents reacted to this change.")
        except ValueError as e:
            st.error(str(e))

    st.divider()

    st.subheader("Simulate a demand drop (for demoing the Pricing Agent)")
    st.caption(
        "Artificially reduces a product's recent sales history. Use this to demonstrate the "
        "Pricing Agent flagging a real discount opportunity live, since the natural dataset may "
        "not always have an overstocked + falling-demand product at any given moment."
    )

    drop_product = st.selectbox("Product to reduce demand for", products_df["name"], key="drop_product")
    reduction = st.slider("Reduce recent sales by (%)", min_value=20, max_value=90, value=60)

    if st.button("Simulate demand drop"):
        pid = int(products_df[products_df["name"] == drop_product]["product_id"].values[0])
        result = simulate_demand_drop(conn, pid, days=14, reduction_pct=reduction)
        st.success(f"Reduced sales across {result['rows_updated']} recent days by {result['reduction_pct']}%.")
        st.cache_data.clear()
        st.info("Now check the Pricing & Discounts page -- this product may now show up as a discount suggestion.")

    st.divider()

    st.subheader("Simulate a customer return")
    st.caption("Creates a pending return request -- then go to the Returns page to see the model compute a live risk score and reasoning for it.")

    RETURN_REASONS = [
        "Item damaged on arrival",
        "Wrong size / doesn't fit",
        "Changed my mind",
        "Item not as described",
        "Received wrong item",
        "Found cheaper elsewhere",
        "Quality not as expected",
    ]

    col1, col2 = st.columns(2)
    with col1:
        return_product = st.selectbox("Product being returned", products_df["name"], key="return_product")
        return_reason = st.selectbox("Return reason", RETURN_REASONS)
    with col2:
        days_ago = st.slider("Days since the order was placed", min_value=1, max_value=60, value=15)

    if st.button("Simulate this return"):
        pid = int(products_df[products_df["name"] == return_product]["product_id"].values[0])
        result = simulate_return(conn, pid, return_reason, days_since_order=days_ago)
        st.success(
            f"Return simulated for {result['product_name']} -- this customer now has "
            f"{result['customer_total_returns']} total return(s) on record."
        )
        st.cache_data.clear()
        st.info("Now go to the Returns page to see the live risk assessment for this return.")

    st.divider()

    st.subheader("Adjust product stock")
    st.caption("Increase a product's stock quantity for demoing inventory/ordering scenarios.")

    stock_product = st.selectbox("Product to increase stock for", products_df["name"], key="stock_product")
    add_qty = st.number_input("Add quantity", min_value=1, max_value=10000, value=50)

    if st.button("Increase stock"):
        pid = int(products_df[products_df["name"] == stock_product]["product_id"].values[0])
        try:
            result = simulate_increase_stock(conn, pid, add_qty)
            st.success(f"Stock increased: {result['product_name']}: {result['old_stock']} -> {result['new_stock']}")
            st.cache_data.clear()
            st.info("Stock updated — check Inventory & Forecast or Pricing & Discounts to see agents react.")
        except Exception as e:
            st.error(str(e))