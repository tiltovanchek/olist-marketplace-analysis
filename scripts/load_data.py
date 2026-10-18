from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "olist.duckdb"

FILES = {
    "customers": "olist_customers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}

def main():
    with duckdb.connect(str(DB_PATH)) as con:
        for table, filename in FILES.items():
            path = DATA_DIR / filename

            if not path.exists():
                raise FileNotFoundError(f"Не найден файл: {path}")

            sql_path = path.as_posix().replace("'", "''")

            con.execute(
                f"""
                CREATE OR REPLACE TABLE {table} AS
                SELECT *
                FROM read_csv_auto(
                    '{sql_path}',
                    header = true,
                    all_varchar = true
                )
                """
            )

            rows = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{table}: {rows:,} строк")

    print(f"\nБаза создана: {DB_PATH}")


if __name__ == "__main__":
    main()