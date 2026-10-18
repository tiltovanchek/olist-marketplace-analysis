from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "olist.duckdb"

with duckdb.connect(str(DB_PATH), read_only=True) as con:
    checks = {
        "Статусы заказов": """
            SELECT order_status, COUNT(*) AS orders_count
            FROM orders
            GROUP BY order_status
            ORDER BY orders_count DESC
        """,
        "Уникальность ID заказов": """
            SELECT
                COUNT(*) AS rows_count,
                COUNT(DISTINCT order_id) AS unique_orders
            FROM orders
        """,
        "Заказы с несколькими позициями": """
            SELECT COUNT(*) AS orders_count
            FROM (
                SELECT order_id
                FROM order_items
                GROUP BY order_id
                HAVING COUNT(*) > 1
            )
        """,
        "Нули": """
            SELECT 
                COUNT(*) FILTER (WHERE order_id IS NULL) AS orders_nulls,
                COUNT(*) FILTER (WHERE order_item_id IS NULL) AS items_nulls
            FROM order_items
        """,
        "Заказы с несколькими платежами": """
            SELECT COUNT(*) AS orders_count
            FROM (
                SELECT order_id
                FROM order_payments
                GROUP BY order_id
                HAVING COUNT(*) > 1
            )
        """,
        "Заказы с несколькими отзывами": """
            SELECT COUNT(*) AS orders_count
            FROM (
                SELECT order_id
                FROM order_reviews
                GROUP BY order_id
                HAVING COUNT(*) > 1
            )
        """,
        "Даты, не приводящиеся к TIMESTAMP": """
            SELECT
                COUNT(*) AS invalid_dates
            FROM orders
            WHERE order_purchase_timestamp IS NOT NULL
              AND TRY_CAST(order_purchase_timestamp AS TIMESTAMP) IS NULL
        """,
        "Пробные sql": """
            SELECT 
                COUNT(*) AS count
            FROM order_items
            WHERE TRY_CAST(freight_value AS DECIMAL) IS NULL
        """,
    }

    for title, query in checks.items():
        print(f"\n{title}")
        print(con.execute(query).df().to_string(index=False))