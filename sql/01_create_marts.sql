CREATE OR REPLACE TABLE mart_orders AS
WITH relevant_order AS (
    SELECT 
        o.order_status AS order_status,
        o.order_id AS order_id,
        c.customer_unique_id AS customer_unique_id,
        TRY_CAST(o.order_purchase_timestamp AS TIMESTAMP) AS order_purchase_timestamp,
        TRY_CAST(o.order_approved_at AS TIMESTAMP) AS order_approved_at,
        TRY_CAST(o.order_delivered_carrier_date AS TIMESTAMP) AS order_delivered_carrier_date,
        TRY_CAST(o.order_delivered_customer_date AS TIMESTAMP) AS order_delivered_customer_date,
        TRY_CAST(o.order_estimated_delivery_date AS TIMESTAMP) AS order_estimated_delivery_date
    FROM orders o
    JOIN customers c ON o.customer_id = c.customer_id
    WHERE 
        o.order_status = 'delivered'
        AND
        TRY_CAST(o.order_purchase_timestamp AS TIMESTAMP) IS NOT NULL
), order_positions AS (
    SELECT
        order_id,
        COUNT(*) AS items_count,
        SUM(TRY_CAST(price AS DECIMAL(10, 2))) AS items_gmv,
        SUM(TRY_CAST(freight_value AS DECIMAL(10, 2))) AS total_freight
    FROM order_items
    GROUP BY order_id
), payments AS (
    SELECT
        order_id,
        COUNT(*) AS total_payments,
        SUM(TRY_CAST(payment_value AS DECIMAL(10, 2))) AS sum_payment
    FROM order_payments
    GROUP BY order_id
), reviews_row AS (
    SELECT
        order_id,
        review_id,
        TRY_CAST(review_score AS INTEGER) AS review_score,
        TRY_CAST(review_creation_date AS TIMESTAMP) AS review_creation_date,
        TRY_CAST(review_answer_timestamp AS TIMESTAMP) AS review_answer_timestamp,
        ROW_NUMBER() OVER (
            PARTITION BY order_id
            ORDER BY 
                TRY_CAST(review_creation_date AS TIMESTAMP) DESC NULLS LAST,
                TRY_CAST(review_answer_timestamp AS TIMESTAMP) DESC NULLS LAST,
                LENGTH(review_comment_message) DESC NULLS LAST,
                review_id ASC
        ) AS sorted
    FROM order_reviews
), reviews AS (
    SELECT *
    FROM reviews_row
    WHERE sorted = 1
)
SELECT
    r.order_id AS order_id,
    r.customer_unique_id AS customer_unique_id,
    r.order_purchase_timestamp AS purchase_ts,
    DATE_TRUNC('month', r.order_purchase_timestamp) AS purchase_month,
    r.order_status AS order_status,
    o.items_count AS items_count,
    o.items_gmv AS items_gmv,
    o.total_freight AS freight_total,
    p.sum_payment AS payment_total,
    r.order_estimated_delivery_date AS estimated_delivery_date,
    r.order_delivered_customer_date AS actual_delivery_ts,
    rv.review_score AS review_score,
    CASE
        WHEN r.order_delivered_customer_date IS NULL
            OR r.order_estimated_delivery_date IS NULL
        THEN NULL
        ELSE CAST(r.order_delivered_customer_date AS DATE)
            > CAST(r.order_estimated_delivery_date AS DATE)
    END AS is_late
FROM relevant_order r 
LEFT JOIN order_positions o ON o.order_id = r.order_id
LEFT JOIN payments p ON p.order_id = r.order_id
LEFT JOIN reviews rv ON rv.order_id = r.order_id;

CREATE OR REPLACE TABLE mart_order_categories AS
WITH categorized_items AS (
    SELECT
        m.order_id,
        TRY_CAST(o.price AS DECIMAL(10, 2)) AS item_price,
        CASE
            WHEN p.product_category_name IS NULL
              OR TRIM(p.product_category_name) = ''
            THEN 'unknown'
            ELSE p.product_category_name
        END AS category_name
    FROM mart_orders AS m
    JOIN order_items AS o ON o.order_id = m.order_id
    LEFT JOIN products AS p ON p.product_id = o.product_id
)
SELECT
    order_id,
    COUNT(*) AS category_items_count,
    SUM(item_price) AS category_gmv,
    category_name
FROM categorized_items
GROUP BY order_id, category_name;