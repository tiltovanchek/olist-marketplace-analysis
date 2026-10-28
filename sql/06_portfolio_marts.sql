-- Витрины портфолио создаются в отдельной БД portfolio.duckdb.
-- analysis_config задаёт начало бизнес-периода и EXCLUSIVE границы наблюдения.
-- Историю до analysis_start сохраняем: иначе первые наблюдаемые покупки
-- существующих покупателей ошибочно стали бы новыми.
-- Статусы отражают снимок выгрузки, а не состояние на историческую дату.

CREATE OR REPLACE TABLE p_orders AS
WITH item_totals AS (
    SELECT order_id,
           COUNT(*) AS items_count,
           SUM(TRY_CAST(price AS DECIMAL(18, 2))) AS items_gmv,
           SUM(TRY_CAST(freight_value AS DECIMAL(18, 2))) AS freight_total,
           COUNT(DISTINCT seller_id) AS seller_count
    FROM order_items
    GROUP BY order_id
), payment_totals AS (
    SELECT order_id,
           SUM(TRY_CAST(payment_value AS DECIMAL(18, 2))) AS payment_total
    FROM order_payments
    GROUP BY order_id
), ranked_reviews AS (
    SELECT order_id,
           TRY_CAST(review_score AS INTEGER) AS review_score,
           TRY_CAST(review_answer_timestamp AS TIMESTAMP) AS review_answer_ts,
           ROW_NUMBER() OVER (
               PARTITION BY order_id
               ORDER BY TRY_CAST(review_creation_date AS TIMESTAMP) DESC NULLS LAST,
                        TRY_CAST(review_answer_timestamp AS TIMESTAMP) DESC NULLS LAST,
                        LENGTH(review_comment_message) DESC NULLS LAST,
                        review_id ASC
           ) AS rn
    FROM order_reviews
), category_totals AS (
    SELECT i.order_id,
           COALESCE(NULLIF(TRIM(t.product_category_name_english), ''),
                    NULLIF(TRIM(p.product_category_name), ''), 'unknown') AS category_name,
           SUM(TRY_CAST(i.price AS DECIMAL(18, 2))) AS category_gmv
    FROM order_items i
    LEFT JOIN products p ON p.product_id = i.product_id
    LEFT JOIN category_translation t
           ON t.product_category_name = p.product_category_name
    GROUP BY i.order_id, category_name
), ranked_categories AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY order_id
        ORDER BY category_gmv DESC NULLS LAST, category_name ASC
    ) AS rn
    FROM category_totals
), typed_orders AS (
    SELECT o.order_id, c.customer_unique_id, c.customer_state,
           TRY_CAST(o.order_purchase_timestamp AS TIMESTAMP) AS purchase_ts,
           o.order_status,
           TRY_CAST(o.order_delivered_customer_date AS TIMESTAMP) AS actual_delivery_ts,
           TRY_CAST(o.order_estimated_delivery_date AS TIMESTAMP) AS estimated_delivery_date
    FROM orders o
    LEFT JOIN customers c ON c.customer_id = o.customer_id
)
SELECT o.order_id, o.customer_unique_id, o.customer_state,
       o.purchase_ts, CAST(DATE_TRUNC('month', o.purchase_ts) AS DATE) AS purchase_month,
       o.order_status,
       COALESCE(i.items_count, 0) AS items_count,
       i.items_gmv, i.freight_total, p.payment_total,
       COALESCE(i.seller_count, 0) AS seller_count,
       COALESCE(cat.category_name, 'unknown') AS primary_category,
       r.review_score, r.review_answer_ts,
       o.actual_delivery_ts, o.estimated_delivery_date,
       CASE WHEN o.actual_delivery_ts IS NULL OR o.estimated_delivery_date IS NULL THEN NULL
            ELSE CAST(o.actual_delivery_ts AS DATE) > CAST(o.estimated_delivery_date AS DATE)
       END AS is_late,
       -- Реальное время в сутках, а не число пересечённых календарных дат.
       EPOCH(o.actual_delivery_ts - o.purchase_ts) / 86400.0 AS delivery_days,
       DATE_DIFF('day', CAST(o.estimated_delivery_date AS DATE),
                       CAST(o.actual_delivery_ts AS DATE)) AS delay_days
FROM typed_orders o
CROSS JOIN analysis_config cfg
LEFT JOIN item_totals i ON i.order_id = o.order_id
LEFT JOIN payment_totals p ON p.order_id = o.order_id
LEFT JOIN ranked_reviews r ON r.order_id = o.order_id AND r.rn = 1
LEFT JOIN ranked_categories cat ON cat.order_id = o.order_id AND cat.rn = 1
WHERE o.purchase_ts < cfg.observation_end;

-- Зерно: исходная товарная позиция (order_id, order_item_id).
-- seller_state описывает местонахождение продавца, customer_state — покупателя.
-- Если перевода нет, сохраняем исходную категорию; пропуски обозначаем unknown.
CREATE OR REPLACE TABLE p_items AS
SELECT i.order_id, i.order_item_id, i.product_id, i.seller_id,
       s.seller_state,
       COALESCE(NULLIF(TRIM(t.product_category_name_english), ''),
                NULLIF(TRIM(p.product_category_name), ''), 'unknown') AS category_name,
       TRY_CAST(i.price AS DECIMAL(18, 2)) AS item_price,
       TRY_CAST(i.freight_value AS DECIMAL(18, 2)) AS freight_value,
       o.purchase_ts, o.order_status, o.customer_unique_id, o.customer_state
FROM order_items i
JOIN p_orders o ON o.order_id = i.order_id
LEFT JOIN products p ON p.product_id = i.product_id
LEFT JOIN category_translation t ON t.product_category_name = p.product_category_name
LEFT JOIN sellers s ON s.seller_id = i.seller_id;

-- Покупатель определяется customer_unique_id; customer_id различается по заказам.
-- «Первый» означает первый наблюдаемый доставленный заказ, не регистрацию.
-- Primary repeat использует даты покупки, а не будущую доставку первого заказа.
-- Покупки в тот же момент допустимы; tie-break order_id задаёт стабильный порядок.
-- Для связи опыта доставки с возвратом нужен отдельный якорь delivery_ts.
-- Обе метрики описательные: выбор только доставленных заказов и неполная
-- история покупателей ограничивают интерпретацию; причинности здесь нет.
CREATE OR REPLACE TABLE p_customers AS
WITH numbered AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY customer_unique_id ORDER BY purchase_ts, order_id
    ) AS rn
    FROM p_orders
    WHERE order_status = 'delivered' AND customer_unique_id IS NOT NULL
), first_second AS (
    SELECT customer_unique_id, COUNT(*) AS delivered_orders,
           MAX(order_id) FILTER (WHERE rn = 1) AS first_order_id,
           MAX(purchase_ts) FILTER (WHERE rn = 1) AS first_purchase_ts,
           MAX(purchase_month) FILTER (WHERE rn = 1) AS first_purchase_month,
           MAX(order_id) FILTER (WHERE rn = 2) AS second_order_id,
           MAX(purchase_ts) FILTER (WHERE rn = 2) AS second_purchase_ts,
           MAX(actual_delivery_ts) FILTER (WHERE rn = 1) AS first_delivery_ts,
           BOOL_OR(is_late) FILTER (WHERE rn = 1) AS is_late,
           MAX(review_score) FILTER (WHERE rn = 1) AS review_score
    FROM numbered
    GROUP BY customer_unique_id
), subsequent AS (
    SELECT f.customer_unique_id,
           MIN(o.purchase_ts) FILTER (WHERE o.purchase_ts > f.first_delivery_ts)
               AS first_repeat_after_delivery_ts,
           MIN(o.purchase_ts) FILTER (
               WHERE o.purchase_ts >= f.first_purchase_ts + INTERVAL '1 day')
               AS first_repeat_after_1d_ts
    FROM first_second f
    LEFT JOIN p_orders o
      ON o.customer_unique_id = f.customer_unique_id
     AND o.order_status = 'delivered'
     AND o.order_id <> f.first_order_id
    GROUP BY f.customer_unique_id
)
SELECT f.*,
       f.first_purchase_ts + INTERVAL '90 days' <= cfg.observation_end AS repeat_90d_eligible,
       CASE WHEN f.first_purchase_ts + INTERVAL '90 days' <= cfg.observation_end
            THEN COALESCE(f.second_purchase_ts < f.first_purchase_ts + INTERVAL '90 days', FALSE)
       END AS repeat_90d,
       COALESCE(f.second_purchase_ts < f.first_purchase_ts + INTERVAL '1 day', FALSE)
           AS repeat_within_1d,
       CASE WHEN f.first_purchase_ts + INTERVAL '90 days' <= cfg.observation_end
            THEN COALESCE(a.first_repeat_after_1d_ts
                          < f.first_purchase_ts + INTERVAL '90 days', FALSE)
       END AS repeat_after_1d_90d,
       COALESCE(f.first_delivery_ts >= f.first_purchase_ts
                AND f.first_delivery_ts + INTERVAL '90 days' <= cfg.observation_end, FALSE)
           AS repeat_after_delivery_90d_eligible,
       CASE WHEN f.first_delivery_ts >= f.first_purchase_ts
                  AND f.first_delivery_ts + INTERVAL '90 days' <= cfg.observation_end
            THEN COALESCE(a.first_repeat_after_delivery_ts
                          < f.first_delivery_ts + INTERVAL '90 days', FALSE)
       END AS repeat_after_delivery_90d,
       a.first_repeat_after_delivery_ts
FROM first_second f
JOIN subsequent a USING (customer_unique_id)
CROSS JOIN analysis_config cfg;
