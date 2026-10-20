import argparse
from pathlib import Path
import duckdb
import sqlparse


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "olist.duckdb"
SQL_DIR = ROOT / "sql"

ALLOWED_FILES = (
    "01_create_marts.sql",
    "02_data_quality.sql",
    "03_metrics.sql",
)


def print_result(result, max_rows=20):
    columns = [column[0] for column in result.description]
    rows = result.fetchmany(max_rows + 1)

    print(" | ".join(columns))

    for row in rows[:max_rows]:
        print(" | ".join(str(value) for value in row))

    if len(rows) > max_rows:
        print(f"... показаны первые {max_rows} строк")
    else:
        print(f"Строк: {len(rows)}")


def main():
    parser = argparse.ArgumentParser(
        description="Выполнить SQL-файл проекта в базе DuckDB."
    )
    parser.add_argument("filename", choices=ALLOWED_FILES)
    args = parser.parse_args()

    sql_path = SQL_DIR / args.filename

    if not DB_PATH.exists():
        raise FileNotFoundError(f"База не найдена: {DB_PATH}")

    if not sql_path.exists():
        raise FileNotFoundError(f"SQL-файл не найден: {sql_path}")

    sql_text = sql_path.read_text(encoding="utf-8")
    statements = sqlparse.parse(sql_text)

    if not statements:
        raise ValueError(f"SQL-файл пуст: {sql_path}")

    print(f"База: {DB_PATH}")
    print(f"SQL-файл: {sql_path.name}")

    with duckdb.connect(str(DB_PATH)) as con:
        for number, statement in enumerate(statements, start=1):
            statement_type = statement.get_type()
            print(f"\nЗапрос {number} ({statement_type}):")

            # Ошибка SQL прервёт выполнение: сообщение об успехе
            # появится только после выполнения всех запросов.
            result = con.execute(str(statement))

            if statement_type == "SELECT":
                print_result(result)
            else:
                print("Выполнен.")

    print(f"\nГотово: выполнено запросов — {len(statements)}")


if __name__ == "__main__":
    main()