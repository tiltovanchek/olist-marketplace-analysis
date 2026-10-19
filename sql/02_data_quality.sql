-- ============================================================
-- 02_data_quality.sql
-- Проверки качества данных и корректности витрин Olist
--
-- Ожидаемое поведение:
--   - структурные проверки и сверки сумм возвращают 'pass';
--   - показатели пропусков описывают данные и не обязаны быть нулевыми;
--   - ошибки преобразования денежных значений должны быть расследованы.
-- ============================================================


-- ============================================================
-- 1. Структура витрин
-- ============================================================

-- 1.1. В mart_orders должна быть одна строка на заказ.
SELECT
    COUNT(*) AS rows_count,
    COUNT(DISTINCT order_id) AS distinct_orders,
    COUNT(*) FILTER (WHERE order_id IS NULL) AS missing_order_id,
    CASE
        WHEN COUNT(*) = COUNT(DISTINCT order_id) THEN 'pass'
        ELSE 'fail'
    END AS status
FROM mart_orders;


-- 1.2. В mart_order_categories должна быть одна строка
-- на пару «заказ × категория».
SELECT
    COUNT(*) AS duplicated_order_category_pairs,
    CASE
        WHEN COUNT(*) = 0 THEN 'pass'
        ELSE 'fail'
    END AS status
FROM (
    SELECT
        order_id,
        category_name
    FROM mart_order_categories
    GROUP BY order_id, category_name
    HAVING COUNT(*) > 1
) AS duplicated_pairs;


-- 1.3. Каждый заказ из mart_orders должен присутствовать
-- в mart_order_categories.
SELECT
    COUNT(*) AS orders_without_category,
    CASE
        WHEN COUNT(*) = 0 THEN 'pass'
        ELSE 'fail'
    END AS status
FROM mart_orders AS m
WHERE NOT EXISTS (
    SELECT 1
    FROM mart_order_categories AS c
    WHERE c.order_id = m.order_id
);


-- 1.4. Обязательные поля и статус заказов.
SELECT
    COUNT(*) FILTER (
        WHERE purchase_ts IS NULL
    ) AS missing_purchase_ts,

    COUNT(*) FILTER (
        WHERE customer_unique_id IS NULL
    ) AS missing_customer_unique_id,

    COUNT(*) FILTER (
        WHERE order_status IS NULL OR order_status <> 'delivered'
    ) AS invalid_order_status
FROM mart_orders;


-- ============================================================
-- 2. Сверка количества позиций и денежных сумм
-- ============================================================

-- 2.1. Итоги по заказам должны совпадать с итогами по категориям.
-- Агрегируем витрины отдельно, чтобы JOIN не размножил показатели.
WITH order_totals AS (
    SELECT
        SUM(items_count) AS items_count,
        SUM(items_gmv) AS gmv
    FROM mart_orders
),
category_totals AS (
    SELECT
        SUM(category_items_count) AS items_count,
        SUM(category_gmv) AS gmv
    FROM mart_order_categories
)
SELECT
    o.items_count AS orders_items_count,
    c.items_count AS categories_items_count,
    o.items_count - c.items_count AS items_count_diff,

    o.gmv AS orders_gmv,
    c.gmv AS categories_gmv,
    o.gmv - c.gmv AS gmv_diff,

    CASE
        WHEN o.items_count IS NOT NULL
         AND c.items_count IS NOT NULL
         AND o.gmv IS NOT NULL
         AND c.gmv IS NOT NULL
         AND o.items_count = c.items_count
         AND o.gmv = c.gmv
        THEN 'pass'
        ELSE 'fail'
    END AS status
FROM order_totals AS o
CROSS JOIN category_totals AS c;


-- 2.2. GMV витрины должен совпадать с суммой цен исходных позиций
-- для заказов, вошедших в mart_orders.
WITH source_totals AS (
    SELECT
        SUM(TRY_CAST(oi.price AS DECIMAL(10, 2))) AS source_gmv
    FROM order_items AS oi
    JOIN mart_orders AS m ON m.order_id = oi.order_id
),
mart_totals AS (
    SELECT
        SUM(items_gmv) AS mart_gmv
    FROM mart_orders
)
SELECT
    s.source_gmv,
    m.mart_gmv,
    s.source_gmv - m.mart_gmv AS gmv_diff,
    CASE
        WHEN s.source_gmv IS NOT NULL
         AND m.mart_gmv IS NOT NULL
         AND s.source_gmv = m.mart_gmv
        THEN 'pass'
        ELSE 'fail'
    END AS status
FROM source_totals AS s
CROSS JOIN mart_totals AS m;


-- 2.3. Сумма платежей витрины должна совпадать с исходными
-- платежами для заказов, вошедших в mart_orders.
WITH source_totals AS (
    SELECT
        SUM(TRY_CAST(op.payment_value AS DECIMAL(10, 2)))
            AS source_payment_total
    FROM order_payments AS op
    JOIN mart_orders AS m ON m.order_id = op.order_id
),
mart_totals AS (
    SELECT
        SUM(payment_total) AS mart_payment_total
    FROM mart_orders
)
SELECT
    s.source_payment_total,
    m.mart_payment_total,
    s.source_payment_total - m.mart_payment_total AS payment_diff,
    CASE
        WHEN s.source_payment_total IS NOT NULL
         AND m.mart_payment_total IS NOT NULL
         AND s.source_payment_total = m.mart_payment_total
        THEN 'pass'
        ELSE 'fail'
    END AS status
FROM source_totals AS s
CROSS JOIN mart_totals AS m;


-- ============================================================
-- 3. Полнота данных и качество преобразования типов
-- ============================================================

-- 3.1. Пропуски в итоговой витрине.
-- Здесь считаются заказы, а не строки исходных таблиц.
SELECT
    COUNT(*) AS orders_total,

    COUNT(*) FILTER (
        WHERE items_count IS NULL
    ) AS orders_without_items_count,

    COUNT(*) FILTER (
        WHERE payment_total IS NULL
    ) AS orders_without_payment_total,

    COUNT(*) FILTER (
        WHERE review_score IS NULL
    ) AS orders_without_review_score,

    COUNT(*) FILTER (
        WHERE estimated_delivery_date IS NULL
    ) AS orders_without_estimated_delivery_date,

    COUNT(*) FILTER (
        WHERE actual_delivery_ts IS NULL
    ) AS orders_without_actual_delivery_date,

    COUNT(*) FILTER (
        WHERE is_late IS NULL
    ) AS orders_without_late_flag,

    ROUND(
        100.0 * COUNT(*) FILTER (WHERE review_score IS NULL)
        / NULLIF(COUNT(*), 0),
        2
    ) AS missing_review_pct
FROM mart_orders;


-- 3.2. Пустые и не приводящиеся к DECIMAL значения в позициях
-- заказов, вошедших в mart_orders.
SELECT
    COUNT(*) AS item_rows_total,

    COUNT(*) FILTER (
        WHERE oi.price IS NULL OR TRIM(oi.price) = ''
    ) AS missing_price,

    COUNT(*) FILTER (
        WHERE oi.price IS NOT NULL
          AND TRIM(oi.price) <> ''
          AND TRY_CAST(oi.price AS DECIMAL(10, 2)) IS NULL
    ) AS invalid_price,

    COUNT(*) FILTER (
        WHERE oi.freight_value IS NULL OR TRIM(oi.freight_value) = ''
    ) AS missing_freight,

    COUNT(*) FILTER (
        WHERE oi.freight_value IS NOT NULL
          AND TRIM(oi.freight_value) <> ''
          AND TRY_CAST(oi.freight_value AS DECIMAL(10, 2)) IS NULL
    ) AS invalid_freight
FROM order_items AS oi
JOIN mart_orders AS m ON m.order_id = oi.order_id;


-- 3.3. Пустые и не приводящиеся к DECIMAL значения в платежах
-- заказов, вошедших в mart_orders.
SELECT
    COUNT(*) AS payment_rows_total,

    COUNT(*) FILTER (
        WHERE op.payment_value IS NULL OR TRIM(op.payment_value) = ''
    ) AS missing_payment_value,

    COUNT(*) FILTER (
        WHERE op.payment_value IS NOT NULL
          AND TRIM(op.payment_value) <> ''
          AND TRY_CAST(op.payment_value AS DECIMAL(10, 2)) IS NULL
    ) AS invalid_payment_value
FROM order_payments AS op
JOIN mart_orders AS m ON m.order_id = op.order_id;