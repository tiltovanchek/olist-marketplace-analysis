from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "olist.duckdb"


def check(name, actual, expected):
    if actual != expected:
        raise AssertionError(
            f"{name}: ожидалось {expected}, получено {actual}"
        )
    print(f"✓ {name}: {actual}")


def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"База не найдена: {DB_PATH}")

    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        # 1. Одна строка на один order_id.
        rows_count, unique_orders = con.execute("""
            SELECT COUNT(*), COUNT(DISTINCT order_id)
            FROM mart_orders
        """).fetchone()

        check("Уникальность order_id", unique_orders, rows_count)

        # 2. Сравнение с исходным отбором relevant_order.
        relevant_count = con.execute("""
            SELECT COUNT(*)
            FROM orders AS o
            JOIN customers AS c
                ON o.customer_id = c.customer_id
            WHERE o.order_status = 'delivered'
              AND TRY_CAST(
                  o.order_purchase_timestamp AS TIMESTAMP
              ) IS NOT NULL
        """).fetchone()[0]

        check("Число строк относительно relevant_order", rows_count, relevant_count)
        check("Ожидаемое число строк", rows_count, 96478)

        # 3. Обязательные поля.
        purchase_nulls, customer_nulls = con.execute("""
            SELECT
                COUNT(*) FILTER (WHERE purchase_ts IS NULL),
                COUNT(*) FILTER (WHERE customer_unique_id IS NULL)
            FROM mart_orders
        """).fetchone()

        check("NULL в purchase_ts", purchase_nulls, 0)
        check("NULL в customer_unique_id", customer_nulls, 0)

        # 4. Отсутствие связанных записей в исходных таблицах.
        # Проверяем через NOT EXISTS, а не по NULL в рассчитанных суммах:
        # сумма может быть NULL и при наличии строки, если значение не привелось к числу.
        missing_items, missing_payments, missing_reviews = con.execute("""
            SELECT
                COUNT(*) FILTER (
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM order_items AS i
                        WHERE i.order_id = m.order_id
                    )
                ),
                COUNT(*) FILTER (
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM order_payments AS p
                        WHERE p.order_id = m.order_id
                    )
                ),
                COUNT(*) FILTER (
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM order_reviews AS r
                        WHERE r.order_id = m.order_id
                    )
                )
            FROM mart_orders AS m
        """).fetchone()

        check("Заказы без позиций", missing_items, 0)
        check("Заказы без платежей", missing_payments, 1)
        check("Заказы без отзывов", missing_reviews, 646)

        # 5. Месяц покупки должен содержать и год, и месяц покупки.
        wrong_months = con.execute("""
            SELECT COUNT(*)
            FROM mart_orders
            WHERE purchase_month IS DISTINCT FROM
                  DATE_TRUNC('month', purchase_ts)
        """).fetchone()[0]

        check("Неверный purchase_month", wrong_months, 0)

        # 6. is_late должен быть BOOLEAN и соответствовать датам.
        is_late_type = con.execute("""
            SELECT typeof(is_late)
            FROM mart_orders
            LIMIT 1
        """).fetchone()[0]

        check("Тип is_late", is_late_type, "BOOLEAN")

        wrong_is_late = con.execute("""
            SELECT COUNT(*)
            FROM mart_orders
            WHERE is_late IS DISTINCT FROM
                CASE
                    WHEN actual_delivery_ts IS NULL
                      OR estimated_delivery_date IS NULL
                    THEN NULL
                    ELSE CAST(actual_delivery_ts AS DATE)
                       > CAST(estimated_delivery_date AS DATE)
                END
        """).fetchone()[0]

        check("Неверный is_late", wrong_is_late, 0)

    print("\nВсе проверки пройдены.")


if __name__ == "__main__":
    main()