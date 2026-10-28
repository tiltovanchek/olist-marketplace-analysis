-- Все проценты даны в шкале 0..100, денежные суммы — BRL.
-- GMV — стоимость товаров доставленных заказов без freight, не выручка площадки.
-- Границы analysis_config EXCLUSIVE; analysis_start включается.
-- Статусы — конечный снимок данных. Это не историческая воронка конверсии.

CREATE OR REPLACE TABLE a_monthly AS
WITH calendar AS (
    SELECT CAST(month_ts AS DATE) AS month
    FROM analysis_config cfg,
         GENERATE_SERIES(CAST(DATE_TRUNC('month', cfg.analysis_start) AS DATE),
                         CAST(cfg.observation_end - INTERVAL '1 day' AS DATE),
                         INTERVAL '1 month') s(month_ts)
), monthly AS (
    SELECT o.purchase_month AS month,
           COUNT(*) AS orders_count,
           COUNT(*) FILTER (WHERE o.order_status = 'delivered') AS delivered_orders,
           COUNT(*) FILTER (WHERE o.order_status = 'canceled') AS canceled_orders,
           COUNT(*) FILTER (WHERE o.order_status = 'unavailable') AS unavailable_orders,
           COUNT(*) FILTER (WHERE o.order_status <> 'delivered') AS not_delivered_orders,
           COUNT(DISTINCT o.customer_unique_id) FILTER (WHERE o.order_status = 'delivered')
               AS delivered_buyers,
           SUM(o.items_gmv) FILTER (WHERE o.order_status = 'delivered') AS delivered_gmv,
           SUM(o.freight_total) FILTER (WHERE o.order_status = 'delivered') AS delivered_freight,
           COUNT(*) FILTER (WHERE o.order_status = 'delivered' AND o.order_id = c.first_order_id)
               AS new_buyer_orders,
           COUNT(*) FILTER (WHERE o.order_status = 'delivered' AND o.order_id <> c.first_order_id)
               AS returning_buyer_orders,
           COUNT(DISTINCT o.customer_unique_id) FILTER (
               WHERE o.order_status = 'delivered' AND c.first_purchase_month = o.purchase_month)
               AS new_buyers,
           COUNT(DISTINCT o.customer_unique_id) FILTER (
               WHERE o.order_status = 'delivered' AND c.first_purchase_month < o.purchase_month)
               AS returning_buyers
    FROM p_orders o
    LEFT JOIN p_customers c USING (customer_unique_id)
    CROSS JOIN analysis_config cfg
    WHERE o.purchase_ts >= cfg.analysis_start
    GROUP BY o.purchase_month
), complete AS (
    SELECT cal.month,
           COALESCE(m.orders_count, 0) AS orders_count,
           COALESCE(m.delivered_orders, 0) AS delivered_orders,
           COALESCE(m.canceled_orders, 0) AS canceled_orders,
           COALESCE(m.unavailable_orders, 0) AS unavailable_orders,
           COALESCE(m.not_delivered_orders, 0) AS not_delivered_orders,
           COALESCE(m.delivered_buyers, 0) AS delivered_buyers,
           COALESCE(m.delivered_gmv, 0) AS delivered_gmv,
           COALESCE(m.delivered_freight, 0) AS delivered_freight,
           COALESCE(m.new_buyer_orders, 0) AS new_buyer_orders,
           COALESCE(m.returning_buyer_orders, 0) AS returning_buyer_orders,
           COALESCE(m.new_buyers, 0) AS new_buyers,
           COALESCE(m.returning_buyers, 0) AS returning_buyers
    FROM calendar cal LEFT JOIN monthly m USING (month)
), lagged AS (
    SELECT *, LAG(delivered_gmv) OVER (ORDER BY month) AS previous_gmv
    FROM complete
)
SELECT *,
       100.0 * delivered_orders / NULLIF(orders_count, 0) AS delivered_rate_pct,
       100.0 * canceled_orders / NULLIF(orders_count, 0) AS canceled_rate_pct,
       100.0 * unavailable_orders / NULLIF(orders_count, 0) AS unavailable_rate_pct,
       delivered_gmv / NULLIF(delivered_orders, 0) AS aov,
       100.0 * delivered_freight / NULLIF(delivered_gmv, 0) AS freight_to_gmv_pct,
       delivered_gmv - previous_gmv AS gmv_change,
       100.0 * (delivered_gmv - previous_gmv) / NULLIF(previous_gmv, 0) AS gmv_mom_pct
FROM lagged ORDER BY month;

CREATE OR REPLACE TABLE a_repeat90 AS
SELECT c.first_purchase_month AS cohort_month,
       COUNT(*) AS cohort_buyers,
       COUNT(*) FILTER (WHERE c.repeat_90d_eligible) AS eligible_buyers,
       COUNT(*) FILTER (WHERE c.repeat_90d) AS repeat_buyers,
       100.0 * COUNT(*) FILTER (WHERE c.repeat_90d)
           / NULLIF(COUNT(*) FILTER (WHERE c.repeat_90d_eligible), 0) AS repeat_rate_pct,
       COUNT(*) FILTER (WHERE c.repeat_90d_eligible AND c.repeat_within_1d)
           AS same_day_repeat_buyers,
       COUNT(*) FILTER (WHERE c.repeat_after_1d_90d) AS repeat_after_1d_buyers,
       -- Чувствительность: те же eligible покупатели; любая покупка в [1d, 90d).
       -- Учитывает третий/следующие заказы даже при втором заказе в первые сутки.
       100.0 * COUNT(*) FILTER (WHERE c.repeat_after_1d_90d)
           / NULLIF(COUNT(*) FILTER (WHERE c.repeat_90d_eligible), 0)
           AS repeat_after_1d_rate_pct,
       COUNT(*) FILTER (WHERE c.repeat_after_delivery_90d_eligible)
           AS delivery_eligible_buyers,
       COUNT(*) FILTER (WHERE c.repeat_after_delivery_90d)
           AS delivery_repeated_buyers,
       100.0 * COUNT(*) FILTER (WHERE c.repeat_after_delivery_90d)
           / NULLIF(COUNT(*) FILTER (WHERE c.repeat_after_delivery_90d_eligible), 0)
           AS repeat_after_delivery_rate_pct
FROM p_customers c CROSS JOIN analysis_config cfg
WHERE c.first_purchase_ts >= cfg.analysis_start
GROUP BY c.first_purchase_month ORDER BY cohort_month;

-- Календарный retention: доля когорты с доставленным заказом в месяце age.
-- Полный месяц нужен целиком: частичные месяцы имеют NULL, а не нулевой retention.
-- Каждый пользователь учитывается максимум один раз в ячейке; age 0 = 100%.
CREATE OR REPLACE TABLE a_retention AS
WITH cohorts AS (
    SELECT c.first_purchase_month AS cohort_month, COUNT(*) AS cohort_buyers
    FROM p_customers c CROSS JOIN analysis_config cfg
    WHERE c.first_purchase_ts >= cfg.analysis_start
    GROUP BY c.first_purchase_month
), grid AS (
    SELECT c.*, age.month_age,
           CAST(c.cohort_month + age.month_age * INTERVAL '1 month' AS DATE) AS activity_month
    FROM cohorts c CROSS JOIN RANGE(0, 7) age(month_age)
), active AS (
    SELECT c.first_purchase_month AS cohort_month,
           o.purchase_month AS activity_month,
           COUNT(DISTINCT c.customer_unique_id) AS retained_buyers
    FROM p_customers c JOIN p_orders o USING (customer_unique_id)
    WHERE o.order_status = 'delivered'
    GROUP BY c.first_purchase_month, o.purchase_month
)
SELECT g.cohort_month, g.month_age, g.activity_month, g.cohort_buyers,
       g.activity_month + INTERVAL '1 month' <= cfg.observation_end AS is_mature,
       CASE WHEN g.activity_month + INTERVAL '1 month' <= cfg.observation_end
            THEN COALESCE(a.retained_buyers, 0) END AS retained_buyers,
       CASE WHEN g.activity_month + INTERVAL '1 month' <= cfg.observation_end
            THEN 100.0 * COALESCE(a.retained_buyers, 0) / NULLIF(g.cohort_buyers, 0)
       END AS retention_pct
FROM grid g LEFT JOIN active a USING (cohort_month, activity_month)
CROSS JOIN analysis_config cfg ORDER BY cohort_month, month_age;

-- Категории считаются по позициям, не по primary_category заказа.
-- Заказ нескольких категорий входит в несколько orders_count; GMV аддитивен.
CREATE OR REPLACE TABLE a_categories AS
WITH totals AS (
    SELECT i.category_name,
           COUNT(*) AS items_count,
           COUNT(DISTINCT i.order_id) AS orders_count,
           COUNT(DISTINCT i.customer_unique_id) AS buyers_count,
           SUM(i.item_price) AS gmv,
           SUM(i.freight_value) AS freight_total
    FROM p_items i CROSS JOIN analysis_config cfg
    WHERE i.order_status = 'delivered' AND i.purchase_ts >= cfg.analysis_start
    GROUP BY i.category_name
)
SELECT *, 100.0 * gmv / NULLIF(SUM(gmv) OVER (), 0) AS gmv_share_pct,
       100.0 * SUM(gmv) OVER (ORDER BY gmv DESC, category_name ROWS UNBOUNDED PRECEDING)
           / NULLIF(SUM(gmv) OVER (), 0) AS cumulative_gmv_share_pct
FROM totals ORDER BY gmv DESC, category_name;

-- Сервисный анализ заканчивается раньше общего бизнес-периода: оставляем
-- запас времени на дозревание доставки/отзыва. Это не исправляет все ограничения
-- снимка и не делает связи между доставкой и оценкой причинными.
CREATE OR REPLACE TABLE a_service_monthly AS
SELECT o.purchase_month AS month, COUNT(*) AS delivered_orders,
       COUNT(*) FILTER (WHERE o.is_late IS NOT NULL) AS known_date_orders,
       COUNT(*) FILTER (WHERE o.is_late) AS late_orders,
       100.0 * COUNT(*) FILTER (WHERE o.is_late)
           / NULLIF(COUNT(*) FILTER (WHERE o.is_late IS NOT NULL), 0) AS late_rate_pct,
       COUNT(*) FILTER (WHERE o.delivery_days >= 0) AS delivery_duration_orders,
       AVG(o.delivery_days) FILTER (WHERE o.delivery_days >= 0) AS avg_delivery_days,
       MEDIAN(o.delivery_days) FILTER (WHERE o.delivery_days >= 0) AS median_delivery_days,
       QUANTILE_CONT(o.delivery_days, 0.9) FILTER (WHERE o.delivery_days >= 0) AS p90_delivery_days,
       QUANTILE_CONT(o.delay_days, 0.9) FILTER (WHERE o.is_late) AS p90_late_days,
       COUNT(*) FILTER (WHERE o.review_score IS NOT NULL) AS reviewed_orders,
       COUNT(*) FILTER (WHERE o.review_score <= 2) AS low_review_orders,
       100.0 * COUNT(*) FILTER (WHERE o.review_score <= 2)
           / NULLIF(COUNT(*) FILTER (WHERE o.review_score IS NOT NULL), 0) AS low_review_rate_pct
FROM p_orders o CROSS JOIN analysis_config cfg
WHERE o.order_status = 'delivered'
  AND o.purchase_ts >= cfg.analysis_start AND o.purchase_ts < cfg.service_end
GROUP BY o.purchase_month ORDER BY month;

-- Минимум 100 заказов с датами уменьшает шум; это не статистическая поправка.
CREATE OR REPLACE TABLE a_service_segments AS
SELECT COALESCE(o.customer_state, 'unknown') AS customer_state,
       COUNT(*) AS delivered_orders,
       COUNT(*) FILTER (WHERE o.is_late IS NOT NULL) AS known_date_orders,
       COUNT(*) FILTER (WHERE o.is_late) AS late_orders,
       100.0 * COUNT(*) FILTER (WHERE o.is_late)
           / NULLIF(COUNT(*) FILTER (WHERE o.is_late IS NOT NULL), 0) AS late_rate_pct,
       MEDIAN(o.delivery_days) FILTER (WHERE o.delivery_days >= 0) AS median_delivery_days,
       QUANTILE_CONT(o.delivery_days, 0.9) FILTER (WHERE o.delivery_days >= 0) AS p90_delivery_days,
       COUNT(*) FILTER (WHERE o.review_score IS NOT NULL) AS reviewed_orders,
       COUNT(*) FILTER (WHERE o.review_score <= 2) AS low_review_orders,
       100.0 * COUNT(*) FILTER (WHERE o.review_score <= 2)
           / NULLIF(COUNT(*) FILTER (WHERE o.review_score IS NOT NULL), 0) AS low_review_rate_pct
FROM p_orders o CROSS JOIN analysis_config cfg
WHERE o.order_status = 'delivered'
  AND o.purchase_ts >= cfg.analysis_start AND o.purchase_ts < cfg.service_end
GROUP BY COALESCE(o.customer_state, 'unknown')
HAVING COUNT(*) FILTER (WHERE o.is_late IS NOT NULL) >= 100
ORDER BY late_rate_pct DESC, customer_state;

CREATE OR REPLACE TABLE a_review_delivery AS
SELECT o.is_late, COUNT(*) AS reviewed_orders,
       AVG(o.review_score) AS avg_review_score,
       COUNT(*) FILTER (WHERE o.review_score <= 2) AS low_review_orders,
       100.0 * COUNT(*) FILTER (WHERE o.review_score <= 2) / NULLIF(COUNT(*), 0)
           AS low_review_rate_pct,
       -- Чувствительность к времени отзыва: только ответы после факта доставки.
       COUNT(*) FILTER (WHERE o.review_answer_ts >= o.actual_delivery_ts)
           AS reviewed_after_delivery_orders,
       COUNT(*) FILTER (WHERE o.review_answer_ts >= o.actual_delivery_ts AND o.review_score <= 2)
           AS low_review_after_delivery_orders,
       AVG(o.review_score) FILTER (WHERE o.review_answer_ts >= o.actual_delivery_ts)
           AS avg_review_after_delivery_score,
       100.0 * COUNT(*) FILTER (
           WHERE o.review_answer_ts >= o.actual_delivery_ts AND o.review_score <= 2)
           / NULLIF(COUNT(*) FILTER (WHERE o.review_answer_ts >= o.actual_delivery_ts), 0)
           AS low_review_after_delivery_rate_pct
FROM p_orders o CROSS JOIN analysis_config cfg
WHERE o.order_status = 'delivered' AND o.is_late IS NOT NULL AND o.review_score IS NOT NULL
  AND o.purchase_ts >= cfg.analysis_start AND o.purchase_ts < cfg.service_end
GROUP BY o.is_late ORDER BY o.is_late;
