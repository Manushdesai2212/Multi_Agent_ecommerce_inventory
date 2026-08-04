-- ============================================================
-- E-commerce Multi-Agent Inventory Management System
-- Database Schema
-- ============================================================

PRAGMA foreign_keys = ON;

-- Product catalog
CREATE TABLE IF NOT EXISTS products (
    product_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    price           REAL NOT NULL,
    stock_qty       INTEGER NOT NULL DEFAULT 0,
    reorder_level   INTEGER NOT NULL DEFAULT 10,
    created_at      TEXT NOT NULL
);

-- Customers
CREATE TABLE IF NOT EXISTS customers (
    customer_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    email               TEXT NOT NULL,
    signup_date         TEXT NOT NULL,
    is_repeat_offender  INTEGER NOT NULL DEFAULT 0  -- 1 = flagged during generation as a serial returner (queryable feature, not just internal)
);

-- Daily sales history (used to train the demand forecasting model)
CREATE TABLE IF NOT EXISTS sales_history (
    sale_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL,
    sale_date       TEXT NOT NULL,
    units_sold      INTEGER NOT NULL,
    is_promo        INTEGER NOT NULL DEFAULT 0,  -- 1 = a promo multiplier was applied this product-day
    is_stockout     INTEGER NOT NULL DEFAULT 0,  -- 1 = actual_sold was capped below intended_demand due to low stock
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

-- Orders placed by customers
CREATE TABLE IF NOT EXISTS orders (
    order_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL,
    product_id      INTEGER NOT NULL,
    order_date      TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    total_amount    REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'completed',  -- completed, returned, cancelled
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

-- Return requests (used to train the return-risk classification model)
CREATE TABLE IF NOT EXISTS returns (
    return_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id        INTEGER NOT NULL,
    customer_id     INTEGER NOT NULL,
    product_id      INTEGER NOT NULL,
    return_date     TEXT NOT NULL,
    reason          TEXT NOT NULL,
    days_since_order INTEGER NOT NULL,
    customer_total_returns INTEGER NOT NULL,  -- how many returns this customer has made (incl. this one)
    is_flagged      INTEGER NOT NULL DEFAULT 0,  -- 1 = suspicious pattern (label for ML model)
    agent_decision  TEXT,                        -- filled in later by the Returns Agent: approve/reject
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

-- Log of every agent decision/action (useful for your report + viva demo)
CREATE TABLE IF NOT EXISTS agent_logs (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    agent_name      TEXT NOT NULL,
    action          TEXT NOT NULL,
    details         TEXT
);

CREATE INDEX IF NOT EXISTS idx_sales_product_date ON sales_history(product_id, sale_date);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_returns_customer ON returns(customer_id);