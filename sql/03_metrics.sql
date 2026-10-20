-- Аналитические метрики по доставленным заказам

-- 1. Помесячная динамика продаж

-- Календарь включает все месяцы между первым и последним
-- месяцами, представленными в mart_orders. Месяцы без заказов
-- остаются в результате с NULL-значениями метрик.

WITH month_bounds AS (
    SELECT
        CAST(MIN(purchase_month) AS DATE) AS first_month,
        CAST(MAX(purchase_month) AS DATE) AS last_month
    FROM mart_orders
),

calendar_months AS (
    SELECT
        CAST(generated_month.month_ts AS DATE) AS month
    FROM month_bounds AS bounds
    CROSS JOIN generate_series(
        bounds.first_month,
        bounds.last_month,
        INTERVAL '1 month'
    ) AS generated_month(month_ts)
),

monthly_metrics AS (
    SELECT
        CAST(purchase_month AS DATE) AS month,
        COUNT(*) AS orders_count,
        COUNT(DISTINCT customer_unique_id) AS buyers_count,
        SUM(items_gmv) AS gmv,

        ROUND(
            SUM(items_gmv) / NULLIF(COUNT(*), 0),
            2
        ) AS avg_order_value,

        ROUND(
            SUM(items_count) / NULLIF(COUNT(*), 0),
            2
        ) AS avg_items_per_order,

        ROUND(
            100.0 * SUM(freight_total) / NULLIF(SUM(items_gmv), 0),
            2
        ) AS freight_to_gmv_pct
    FROM mart_orders
    GROUP BY CAST(purchase_month AS DATE)
),

monthly_with_previous AS (
    SELECT
        calendar.month,
        metrics.orders_count,
        metrics.buyers_count,
        metrics.gmv,
        metrics.avg_order_value,
        metrics.avg_items_per_order,
        metrics.freight_to_gmv_pct,
        LAG(metrics.gmv) OVER (
            ORDER BY calendar.month
        ) AS previous_gmv
    FROM calendar_months AS calendar
    LEFT JOIN monthly_metrics AS metrics
        ON metrics.month = calendar.month
)

SELECT
    month,
    orders_count,
    buyers_count,
    gmv,
    avg_order_value,
    avg_items_per_order,
    freight_to_gmv_pct,
    previous_gmv,
    gmv - previous_gmv AS gmv_change
FROM monthly_with_previous
ORDER BY month;


-- 2. Повторные покупки среди доставленных заказов

-- Повтором считается второй доставленный заказ покупателя,
-- совершённый не позднее чем через 90 суток после первого.
-- В расчёт входят только покупатели, для которых к концу
-- периода наблюдения прошло полное 90-дневное окно.

-- Конец периода наблюдения определяется как максимальная
-- корректная дата покупки в исходной таблице orders.

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

observation_end AS (
    SELECT
        MAX(TRY_CAST(order_purchase_timestamp AS TIMESTAMP)) AS max_ts
    FROM orders
),

customer_order_dates AS (
    SELECT
        customer_unique_id,
        MAX(purchase_ts) FILTER (
            WHERE order_number = 1
        ) AS first_order,
        MAX(purchase_ts) FILTER (
            WHERE order_number = 2
        ) AS second_order
    FROM numbered_orders
    GROUP BY customer_unique_id
),

customer_flags AS (
    SELECT
        customer_orders.customer_unique_id,
        customer_orders.first_order,
        customer_orders.second_order,
        CAST(
            DATE_TRUNC('month', customer_orders.first_order)
            AS DATE
        ) AS month_fo,

        customer_orders.first_order + INTERVAL '90 days'
            <= observation.max_ts AS has_full_90d_window,

        customer_orders.second_order IS NOT NULL
            AND customer_orders.second_order
                <= customer_orders.first_order + INTERVAL '90 days'
            AS repeated_90d
    FROM customer_order_dates AS customer_orders
    CROSS JOIN observation_end AS observation
)

SELECT
    month_fo,
    COUNT(*) AS suitable_buyers,
    COUNT(*) FILTER (
        WHERE repeated_90d
    ) AS repeated_buyers,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE repeated_90d)
        / NULLIF(COUNT(*), 0),
        2
    ) AS share_repeat_purchases
FROM customer_flags
WHERE has_full_90d_window
GROUP BY month_fo
ORDER BY month_fo;

-- 3. Продажи по категориям товаров:
-- GMV, количество позиций, число заказов и доля категории в общем GMV.
-- Один заказ может относиться к нескольким категориям;
-- товары без сопоставленной категории учитываются как 'unknown'.
WITH category_totals AS (
    SELECT
        category_name,
        SUM(category_gmv) AS sum_gmv,
        SUM(category_items_count) AS items_count,
        COUNT(DISTINCT order_id) AS num_orders
    FROM mart_order_categories
    GROUP BY category_name
),
overall_total AS (
    SELECT
        SUM(sum_gmv) AS total_gmv
    FROM category_totals
)
SELECT
    c.category_name,
    c.sum_gmv,
    c.items_count,
    c.num_orders,
    ROUND(
        100.0 * c.sum_gmv / NULLIF(t.total_gmv, 0),
        4
    ) AS gmv_share_pct -- в процентах
FROM category_totals AS c
CROSS JOIN overall_total AS t
ORDER BY c.sum_gmv DESC;