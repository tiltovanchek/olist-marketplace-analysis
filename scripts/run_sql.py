from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "olist.duckdb"
SQL_PATH = ROOT / "sql" / "01_create_marts.sql"

def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"База не найдена: {DB_PATH}")

    if not SQL_PATH.exists():
        raise FileNotFoundError(f"SQL-файл не найден: {SQL_PATH}")

    sql = SQL_PATH.read_text(encoding="utf-8")

    with duckdb.connect(str(DB_PATH)) as con:
        con.execute(sql)

    with duckdb.connect(str(DB_PATH)) as con:
        con.execute(sql)
        print("База:", DB_PATH)
        print("Таблицы:", con.execute("SHOW TABLES").fetchall())
        print("Строк в mart_orders:",
            con.execute("SELECT COUNT(*) FROM mart_orders").fetchone()[0])

    print(f"SQL-файл выполнен: {SQL_PATH}")

if __name__ == '__main__':
    main()