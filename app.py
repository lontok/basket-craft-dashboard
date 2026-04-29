import os
from datetime import date

import altair as alt
import pandas as pd
import snowflake.connector
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Basket Craft Dashboard", layout="wide")
st.title("Basket Craft Dashboard")


@st.cache_resource
def get_connection():
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        role=os.environ["SNOWFLAKE_ROLE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=os.environ["SNOWFLAKE_SCHEMA"],
    )


@st.cache_data(ttl=600)
def load_headline_metrics() -> pd.DataFrame:
    sql = """
        WITH bounds AS (
            SELECT MAX(date_key) AS max_date
            FROM analytics.fct_order_items
        ),
        months AS (
            SELECT
                CASE WHEN max_date = LAST_DAY(max_date)
                     THEN DATE_TRUNC('month', max_date)
                     ELSE DATEADD('month', -1, DATE_TRUNC('month', max_date))
                END AS current_month,
                CASE WHEN max_date = LAST_DAY(max_date)
                     THEN DATEADD('month', -1, DATE_TRUNC('month', max_date))
                     ELSE DATEADD('month', -2, DATE_TRUNC('month', max_date))
                END AS prior_month
            FROM bounds
        )
        SELECT
            m.current_month,
            m.prior_month,
            SUM(CASE WHEN DATE_TRUNC('month', f.date_key) = m.current_month
                     THEN f.net_revenue_usd ELSE 0 END) AS revenue_curr,
            SUM(CASE WHEN DATE_TRUNC('month', f.date_key) = m.prior_month
                     THEN f.net_revenue_usd ELSE 0 END) AS revenue_prior,
            COUNT(DISTINCT CASE WHEN DATE_TRUNC('month', f.date_key) = m.current_month
                                THEN f.order_id END) AS orders_curr,
            COUNT(DISTINCT CASE WHEN DATE_TRUNC('month', f.date_key) = m.prior_month
                                THEN f.order_id END) AS orders_prior,
            COUNT(CASE WHEN DATE_TRUNC('month', f.date_key) = m.current_month
                       THEN 1 END) AS items_curr,
            COUNT(CASE WHEN DATE_TRUNC('month', f.date_key) = m.prior_month
                       THEN 1 END) AS items_prior
        FROM analytics.fct_order_items f
        CROSS JOIN months m
        WHERE f.date_key >= m.prior_month
          AND f.date_key < DATEADD('month', 1, m.current_month)
        GROUP BY 1, 2
    """
    with get_connection().cursor() as cur:
        cur.execute(sql)
        cols = [c[0].lower() for c in cur.description]
        row = cur.fetchone()
    return pd.DataFrame([row], columns=cols)


@st.cache_data(ttl=600)
def load_daily_revenue() -> pd.DataFrame:
    sql = """
        SELECT date_key AS day,
               SUM(net_revenue_usd) AS revenue
        FROM analytics.fct_order_items
        GROUP BY day
        ORDER BY day
    """
    with get_connection().cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["day", "revenue"])
    df["day"] = pd.to_datetime(df["day"])
    df["revenue"] = df["revenue"].astype(float)
    return df


@st.cache_data(ttl=600)
def load_product_daily_revenue() -> pd.DataFrame:
    sql = """
        SELECT f.date_key AS day,
               p.product_name,
               SUM(f.net_revenue_usd) AS revenue
        FROM analytics.fct_order_items f
        JOIN analytics.dim_product p ON p.product_id = f.product_id
        GROUP BY f.date_key, p.product_name
    """
    with get_connection().cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["day", "product_name", "revenue"])
    df["day"] = pd.to_datetime(df["day"])
    df["revenue"] = df["revenue"].astype(float)
    return df


@st.cache_data(ttl=600)
def load_products() -> pd.DataFrame:
    sql = "SELECT product_id, product_name FROM analytics.dim_product ORDER BY product_name"
    with get_connection().cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["product_id", "product_name"])


@st.cache_data(ttl=600)
def load_co_purchases(anchor_product_id: int) -> pd.DataFrame:
    sql = """
        WITH anchor_orders AS (
            SELECT DISTINCT order_id
            FROM analytics.fct_order_items
            WHERE product_id = %(anchor_id)s
        )
        SELECT p.product_name,
               COUNT(DISTINCT f.order_id) AS co_orders,
               (SELECT COUNT(*) FROM anchor_orders) AS anchor_orders
        FROM analytics.fct_order_items f
        JOIN anchor_orders a ON a.order_id = f.order_id
        JOIN analytics.dim_product p ON p.product_id = f.product_id
        WHERE f.product_id != %(anchor_id)s
        GROUP BY p.product_name
        ORDER BY co_orders DESC
    """
    with get_connection().cursor() as cur:
        cur.execute(sql, {"anchor_id": anchor_product_id})
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["product_name", "co_orders", "anchor_orders"])
    df["co_orders"] = df["co_orders"].astype(int)
    df["anchor_orders"] = df["anchor_orders"].astype(int)
    return df


@st.cache_data(ttl=600)
def dim_product_row_count() -> int:
    with get_connection().cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM analytics.dim_product")
        return cur.fetchone()[0]


def fmt_month(d: date) -> str:
    return pd.Timestamp(d).strftime("%b %Y")


def safe_aov(revenue, orders) -> float:
    return float(revenue) / orders if orders else 0.0


metrics = load_headline_metrics().iloc[0]

current_month = metrics["current_month"]
prior_month = metrics["prior_month"]

revenue_curr = float(metrics["revenue_curr"])
revenue_prior = float(metrics["revenue_prior"])
orders_curr = int(metrics["orders_curr"])
orders_prior = int(metrics["orders_prior"])
items_curr = int(metrics["items_curr"])
items_prior = int(metrics["items_prior"])

aov_curr = safe_aov(revenue_curr, orders_curr)
aov_prior = safe_aov(revenue_prior, orders_prior)

st.caption(f"Comparing **{fmt_month(current_month)}** vs **{fmt_month(prior_month)}**")

col1, col2, col3, col4 = st.columns(4)
col1.metric(
    "Total revenue",
    f"${revenue_curr:,.0f}",
    delta=f"${revenue_curr - revenue_prior:,.0f}",
)
col2.metric(
    "Total orders",
    f"{orders_curr:,}",
    delta=f"{orders_curr - orders_prior:,}",
)
col3.metric(
    "Average order value",
    f"${aov_curr:,.2f}",
    delta=f"${aov_curr - aov_prior:,.2f}",
)
col4.metric(
    "Total items sold",
    f"{items_curr:,}",
    delta=f"{items_curr - items_prior:,}",
)

daily = load_daily_revenue()
min_day = daily["day"].min().date()
max_day = daily["day"].max().date()
today = date.today()
default_start = max(min_day, (daily["day"].max() - pd.Timedelta(days=89)).date())

with st.sidebar:
    st.header("Filters")
    date_range = st.date_input(
        "Date range",
        value=(default_start, max_day),
        min_value=min_day,
        max_value=today,
    )
    st.caption(f"Data through {max_day:%b %d, %Y} · selections beyond that are clamped.")

if isinstance(date_range, tuple) and len(date_range) == 2:
    start, end = date_range
else:
    start, end = default_start, max_day

start = max(start, min_day)
end = min(end, max_day)

mask = (daily["day"] >= pd.Timestamp(start)) & (daily["day"] <= pd.Timestamp(end))
filtered = daily.loc[mask].set_index("day")

st.subheader("Revenue trend")
st.caption(f"Showing **{start:%b %d, %Y}** – **{end:%b %d, %Y}**")

if filtered.empty:
    st.info("No revenue in the selected range.")
else:
    st.line_chart(filtered["revenue"], y_label="Net revenue (USD)", x_label="Day")
    st.caption(
        f"{len(filtered):,} days · "
        f"total ${filtered['revenue'].sum():,.0f} · "
        f"daily avg ${filtered['revenue'].mean():,.0f}"
    )

st.subheader("Top products by revenue")

product_daily = load_product_daily_revenue()
prod_mask = (
    (product_daily["day"] >= pd.Timestamp(start))
    & (product_daily["day"] <= pd.Timestamp(end))
)
top_products = (
    product_daily.loc[prod_mask]
    .groupby("product_name", as_index=True)["revenue"]
    .sum()
    .sort_values(ascending=False)
)

if top_products.empty:
    st.info("No product revenue in the selected range.")
else:
    top_df = top_products.reset_index()
    chart = (
        alt.Chart(top_df)
        .mark_bar()
        .encode(
            x=alt.X("revenue:Q", title="Net revenue (USD)"),
            y=alt.Y("product_name:N", sort="-x", title="Product"),
            tooltip=[
                alt.Tooltip("product_name:N", title="Product"),
                alt.Tooltip("revenue:Q", title="Net revenue", format="$,.0f"),
            ],
        )
    )
    st.altair_chart(chart, use_container_width=True)

st.subheader("Bundle finder")
st.caption("Pick a product to see what gets bought with it most often, ranked by orders containing both. Computed across all-time data.")

products = load_products()
anchor_label = st.selectbox("Anchor product", products["product_name"])
anchor_id = int(
    products.loc[products["product_name"] == anchor_label, "product_id"].iloc[0]
)

co = load_co_purchases(anchor_id)

if co.empty:
    st.info(f"No other products appear in orders that included **{anchor_label}**.")
else:
    anchor_orders = int(co["anchor_orders"].iloc[0])
    bundled_orders = int(co["co_orders"].sum())
    attach_rate = bundled_orders / anchor_orders if anchor_orders else 0.0
    st.caption(
        f"**{anchor_label}** appears in **{anchor_orders:,}** orders. "
        f"**{bundled_orders:,}** ({attach_rate:.1%}) of those also contained another product."
    )

    bundle_chart = (
        alt.Chart(co)
        .mark_bar()
        .encode(
            x=alt.X("co_orders:Q", title="Orders with both"),
            y=alt.Y("product_name:N", sort="-x", title="Bought with"),
            tooltip=[
                alt.Tooltip("product_name:N", title="Product"),
                alt.Tooltip("co_orders:Q", title="Orders with both", format=","),
            ],
        )
    )
    st.altair_chart(bundle_chart, use_container_width=True)

with st.expander("Snowflake smoke test"):
    st.metric("analytics.dim_product rows", f"{dim_product_row_count():,}")
