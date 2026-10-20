-- Показатели доставки по месяцу покупки заказа.
-- Среднее время доставки представлено как интервал.

WITH monthly_delivery AS (
    SELECT
        CAST(purchase_month AS DATE) AS purchase_month,
        COUNT(*) AS delivered_cnt,

        COUNT(*) FILTER (
            WHERE is_late IS NOT NULL
        ) AS not_null_date,

        COUNT(*) FILTER (
            WHERE is_late IS TRUE
        ) AS late,

        AVG(actual_delivery_ts - purchase_ts) FILTER (
            WHERE actual_delivery_ts IS NOT NULL
              AND purchase_ts IS NOT NULL
        ) AS avg_delivery_time
    FROM mart_orders
    GROUP BY CAST(purchase_month AS DATE)
)

SELECT
    purchase_month,
    delivered_cnt,
    not_null_date,
    late,
    ROUND(
        100.0 * late / NULLIF(not_null_date, 0),
        3
    ) AS late_rate,
    avg_delivery_time
FROM monthly_delivery
ORDER BY purchase_month;