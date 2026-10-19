from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "olist.duckdb"

with duckdb.connect(str(DB_PATH), read_only=True) as con:
    checks = {
        "Пробные sql": """
            WITH o_items AS (
                SELECT 
                    SUM(TRY_CAST(price AS DECIMAL(10, 2))) AS price_total,
                FROM order_items o 
                JOIN mart_orders m ON m.order_id = o.order_id
            ), m_orders AS (
                SELECT
                    SUM(items_gmv) AS sum_items_gmv
                FROM mart_orders
            )
            SELECT
                o.price_total - m.sum_items_gmv AS diff
            FROM o_items o 
            CROSS JOIN m_orders m;
        """,
        "Пробные sql2": """
            SELECT 
                SUM(items_gmv)
            FROM mart_orders
        """,
    }

    for title, query in checks.items():
        print(f"\n{title}")
        print(con.execute(query).df().to_string(index=False))