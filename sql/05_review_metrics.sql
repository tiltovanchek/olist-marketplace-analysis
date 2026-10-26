-- Оценки отзывов

-- В mart_orders хранится одна выбранная оценка на заказ,
-- а не среднее всех отзывов по заказу. Отзыв выбирался по
-- правилу, описанному в 01_create_marts.sql.

-- Доли оценок рассчитываются только среди заказов
-- с известной оценкой. Заказы без оценки показываются отдельно.

WITH review_counts AS (
    SELECT
        COUNT(*) AS orders_total,

        COUNT(*) FILTER (
            WHERE review_score IS NOT NULL
        ) AS orders_with_review,

        COUNT(*) FILTER (
            WHERE review_score IS NULL
        ) AS orders_without_review,

        COUNT(*) FILTER (
            WHERE review_score = 1
        ) AS orders_score_1,

        COUNT(*) FILTER (
            WHERE review_score = 2
        ) AS orders_score_2,

        COUNT(*) FILTER (
            WHERE review_score = 3
        ) AS orders_score_3,

        COUNT(*) FILTER (
            WHERE review_score = 4
        ) AS orders_score_4,

        COUNT(*) FILTER (
            WHERE review_score = 5
        ) AS orders_score_5
    FROM mart_orders
)

SELECT
    orders_total,
    orders_with_review,
    orders_without_review,

    orders_score_1,
    ROUND(
        100.0 * orders_score_1 / NULLIF(orders_with_review, 0),
        3
    ) AS share_score_1_pct,

    orders_score_2,
    ROUND(
        100.0 * orders_score_2 / NULLIF(orders_with_review, 0),
        3
    ) AS share_score_2_pct,

    orders_score_3,
    ROUND(
        100.0 * orders_score_3 / NULLIF(orders_with_review, 0),
        3
    ) AS share_score_3_pct,

    orders_score_4,
    ROUND(
        100.0 * orders_score_4 / NULLIF(orders_with_review, 0),
        3
    ) AS share_score_4_pct,

    orders_score_5,
    ROUND(
        100.0 * orders_score_5 / NULLIF(orders_with_review, 0),
        3
    ) AS share_score_5_pct
FROM review_counts;


-- Средняя оценка по своевременности доставки

-- В расчёт входят только заказы, для которых известны
-- и оценка, и признак своевременности доставки.

-- is_late = FALSE — доставлено вовремя;
-- is_late = TRUE  — доставлено с опозданием.

SELECT
    is_late,
    COUNT(*) AS orders_count,
    ROUND(AVG(review_score), 2) AS avg_review_score
FROM mart_orders
WHERE is_late IS NOT NULL
  AND review_score IS NOT NULL
GROUP BY is_late
ORDER BY is_late;

-- Данные на уровне заказа для сравнения оценок.
-- У одного покупателя может быть несколько заказов;
-- в Python бутстрап будет выполняться по покупателям.

SELECT
    customer_unique_id,
    is_late,
    review_score,
    order_id
FROM mart_orders
WHERE is_late IS NOT NULL
  AND review_score IS NOT NULL
  AND customer_unique_id IS NOT NULL;