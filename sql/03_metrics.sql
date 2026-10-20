-- Сформируем календарь месяцев (так как в данных отсутствует один месяц)
WITH month_bounds AS (
    SELECT
        MIN(CAST(purchase_month AS DATE)) AS first_month,
        MAX(CAST(purchase_month AS DATE)) AS last_month
    FROM mart_orders
), 
calendar_months AS (
    SELECT
        CAST(gs.month_ts AS DATE) AS month
    FROM month_bounds AS b
    CROSS JOIN generate_series(
        b.first_month,
        b.last_month,
        INTERVAL '1 month'
    ) AS gs(month_ts)
), 
monthly_metrics AS(
    SELECT
        CAST(purchase_month AS DATE) AS month,
        COUNT(*) AS orders_count,
        COUNT(DISTINCT customer_unique_id) AS buyers_count,
        SUM(items_gmv) AS gmv,
        ROUND(gmv / NULLIF(orders_count, 0), 2) AS avg_order_value,
        ROUND(SUM(items_count) / NULLIF(orders_count, 0), 2) AS avg_items_per_order, -- заказов без позиций нет, так что это именно среднее число позиций на заказ
        ROUND(SUM(freight_total) * 100 / NULLIF(gmv, 0), 2) AS freight_to_gmv_pct
    FROM mart_orders
    GROUP BY month
)
-- Помесячная динамика продаж
SELECT
    c.month,
    m.orders_count AS orders_count,
    m.buyers_count AS buyers_count,
    m.gmv,
    m.avg_order_value AS avg_order_value,
    m.avg_items_per_order AS avg_items_per_order,
    m.freight_to_gmv_pct AS freight_to_gmv_pct,
    LAG(m.gmv) OVER (ORDER BY c.month) AS previous_gmv,
    m.gmv - previous_gmv AS gmv_change
FROM calendar_months c
LEFT JOIN monthly_metrics m ON m.month = c.month
ORDER BY c.month;

--  Повторные покупки среди доставленных заказов
WITH numbered_orders AS (
    SELECT
        customer_unique_id,
        order_id,
        purchase_ts,
        ROW_NUMBER() OVER (
            PARTITION BY customer_unique_id
            ORDER BY purchase_ts, order_id
        ) AS order_number
    FROM mart_orders
), 
max_purchase_ts AS (
    SELECT MAX(TRY_CAST(order_purchase_timestamp AS TIMESTAMP)) AS max_ts FROM orders
), 
orders_date AS (
    SELECT 
        customer_unique_id,
        MAX(purchase_ts) FILTER (WHERE order_number = 1) AS first_order,
        MAX(purchase_ts) FILTER (WHERE order_number = 2) AS second_order
    FROM numbered_orders
    GROUP BY customer_unique_id
), 
customers_flags AS (
    SELECT 
        o.customer_unique_id,
        o.first_order,
        o.second_order,
        DATE_TRUNC('month', o.first_order) AS month_fo,
        CASE
            WHEN o.first_order + INTERVAL '90 days' <= m.max_ts
            THEN TRUE
            ELSE FALSE
        END AS has_full_90d_window,
        CASE
            WHEN o.second_order <= o.first_order + INTERVAL '90 days'
                AND o.second_order IS NOT NULL
            THEN TRUE
            ELSE FALSE
        END AS repeated_90d
    FROM orders_date o 
    CROSS JOIN max_purchase_ts m
)
SELECT
    CAST(month_fo AS DATE) AS month_fo,
    COUNT(*) AS suitable_buyers,
    COUNT(*) FILTER (WHERE repeated_90d) AS repeated_buyers,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE repeated_90d)
        / NULLIF(COUNT(*), 0),
        2
    ) AS share_repeat_purchases
FROM customers_flags
WHERE has_full_90d_window
GROUP BY month_fo
ORDER BY month_fo;