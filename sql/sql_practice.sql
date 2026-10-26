users
    user_id         — уникальный идентификатор пользователя
    registered_at   — timestamp регистрации
    channel         — канал привлечения, без пропусков

orders
    order_id        — уникальный идентификатор заказа
    user_id         — пользователь
    paid_at         — timestamp оплаты; NULL для неоплаченного заказа

order_items
    order_id        — заказ
    product_id      — товар
    quantity        — количество единиц
    unit_price      — цена одной единицы после скидок

| Поле | Расчёт |
|---|---|
| `users_cnt` | Количество зарегистрированных пользователей |
| `buyers_7d` | Пользователи хотя бы с одной оплатой в первые семь суток |
| `orders_7d` | Количество оплаченных заказов в этом окне |
| `revenue_7d` | Выручка по этим заказам: сумма `quantity × unit_price` |
| `CR_7d` | **Conversion Rate:** `buyers_7d / users_cnt` |
| `AOV_7d` | **Average Order Value:** `revenue_7d / orders_7d` |
| `ARPU_7d` | **Average Revenue per User:** `revenue_7d / users_cnt` |

WITH cohort_users AS (
    SELECT user_id, registered_at, channel
    FROM users
    WHERE registered_at >= TIMESTAMP '2026-01-01'
      AND registered_at <  TIMESTAMP '2026-02-01'
), buy_in_window AS (
    SELECT
        u.channel AS channel,
        o.order_id AS order_id,
        u.user_id AS user_id,
        CASE
            WHEN o.paid_at >= u.registered_at
            AND o.paid_at < u.registered_at + INTERVAL '7 days'
            THEN 1
            ELSE 0
        END AS flag
    FROM orders o
    JOIN cohort_users u ON u.user_id = o.user_id
), revenue_7d AS (
    SELECT
        b.channel AS channel,
        SUM(o.quantity * o.unit_price) AS revenue_7d
    FROM order_items o
    LEFT JOIN buy_in_window b ON o.order_id = b.order_id
    WHERE b.flag = 1
    GROUP BY b.channel
), users_cnt AS (
    SELECT
        channel,
        COUNT(user_id) AS users_cnt
    FROM cohort_users
    GROUP BY channel
), buyers_7d AS (
    SELECT
        channel,
        COUNT(DISTINCT user_id) AS buyers_7d
    FROM buy_in_window
    WHERE flag = 1
    GROUP BY channelыыы
), orders_7d AS (
    SELECT
        channel,
        COUNT(order_id) AS orders_7d
    FROM buy_in_window
    WHERE flag = 1
    GROUP BY channel
)
SELECT
    u.channel,
    u.users_cnt,
    COALESCE(b.buyers_7d, 0) AS buyers_7d,
    COALESCE(o.orders_7d, 0) AS orders_7d,
    COALESCE(r.revenue_7d, 0) AS revenue_7d,

    ROUND(
        COALESCE(b.buyers_7d, 0)::NUMERIC
        / NULLIF(u.users_cnt, 0),
        2
    ) AS CR_7d,

    ROUND(
        COALESCE(r.revenue_7d, 0)
        / NULLIF(o.orders_7d, 0),
        2
    ) AS AOV_7d,

    ROUND(
        COALESCE(r.revenue_7d, 0)
        / NULLIF(u.users_cnt, 0),
        2
    ) AS ARPU_7d

FROM users_cnt u
LEFT JOIN buyers_7d b ON u.channel = b.channel
LEFT JOIN orders_7d o ON u.channel = o.channel
LEFT JOIN revenue_7d r ON u.channel = r.channel;

WITH rn_cte AS (
    SELECT
        user_id,
        order_id,
        paid_at,
        ROW_NUMBER() OVER (
            PARTITION BY user_id
            ORDER BY paid_at, order_id
        ) AS rn
    FROM orders
)
SELECT * 
FROM rn_cte
WHERE rn = 1

WITH paid_with_previous AS (
    SELECT
        user_id,
        order_id,
        paid_at,
        LAG(paid_at) OVER (
            PARTITION BY user_id
            ORDER BY paid_at, order_id
        ) AS previous_paid_at
    FROM orders
    WHERE paid_at IS NOT NULL
)
SELECT
    user_id,
    order_id,
    paid_at,
    previous_paid_at,
    paid_at - previous_paid_at AS time_since_previous
FROM paid_with_previous
ORDER BY user_id, paid_at, order_id;

WITH revenues AS (
    SELECT
        order_id,
        SUM(quantity * unit_price) AS order_revenue
    FROM order_items
    GROUP BY order_id
)
SELECT
    o.user_id,
    o.order_id,
    o.paid_at,
    r.order_revenue,
    SUM(r.order_revenue) OVER (
        PARTITION BY o.user_idы
        ORDER BY paid_at
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW 
    ) AS cumulative_revenue
FROM orders o 
JOIN revenues r ON r.order_id = o.order_id
WHERE paid_at IS NOT NULL 
ORDER BY paid_at, order_id;

SELECT u.city AS city
FROM users u 
JOIN views v ON v.user_id = u.user_id
WHERE v.status = 'completed'
GROUP BY u.city
ORDER BY COUNT(*) DESC, u.city 
LIMIT 1;

SELECT AVG(rating) AS avg 
FROM reviews
WHERE device = 'tv';

SELECT movie_title AS title 
FROM views
GROUP BY movie_title
HAVING COUNT(user_id) >= 2
ORDER BY AVG(duration_min) DESC, movie_title ASC 
LIMIT 1;