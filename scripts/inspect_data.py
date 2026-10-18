from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "olist.duckdb"

with duckdb.connect(str(DB_PATH), read_only=True) as con:
    checks = {
        "Пробные sql": """
            SELECT 
                order_id,
                category_name,
                COUNT(*)
            FROM mart_order_categories
            GROUP BY order_id, category_name
            HAVING COUNT(*) > 1
        """,
    }

    for title, query in checks.items():
        print(f"\n{title}")
        print(con.execute(query).df().to_string(index=False))