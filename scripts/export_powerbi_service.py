"""Export order-level service facts and a daily calendar for Power BI.

Run after build_portfolio.py. The source database is opened read-only.
"""

from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys

import duckdb
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "portfolio.duckdb"
OUTPUT = ROOT / "data" / "powerbi"


def export_csv(frame, filename):
    destination = OUTPUT / filename
    temporary = destination.with_suffix(".csv.tmp")
    # BOM makes Cyrillic status labels identifiable as UTF-8 in Text/CSV import.
    frame.to_csv(temporary, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    temporary.replace(destination)
    return {"rows": len(frame), "bytes": destination.stat().st_size,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not DB.is_file():
        raise FileNotFoundError("Сначала выполните python scripts/build_portfolio.py")

    with duckdb.connect(str(DB), read_only=True) as con:
        config_rows = con.execute("SELECT analysis_start, service_end, observation_end FROM analysis_config").fetchall()
        if len(config_rows) != 1:
            raise AssertionError("analysis_config должна содержать одну строку")
        start, end, observation_end = config_rows[0]
        if not start < end <= observation_end:
            raise AssertionError("Некорректные временные границы")
        orders = con.execute("""
            SELECT order_id, purchase_ts::DATE AS purchase_date,
              coalesce(customer_state, 'unknown') AS customer_state,
              CASE WHEN is_late IS NULL THEN 'Неизвестно'
                   WHEN is_late THEN 'С опозданием' ELSE 'Вовремя' END AS delivery_status,
              is_late::INTEGER AS is_late,
              CASE WHEN delivery_days >= 0 THEN delivery_days END AS delivery_days,
              review_score,
              CASE WHEN review_score IS NOT NULL THEN (review_score <= 2)::INTEGER END AS is_low_score,
              CASE WHEN review_score IS NOT NULL AND actual_delivery_ts IS NOT NULL
                        AND review_answer_ts IS NOT NULL
                   THEN (review_answer_ts >= actual_delivery_ts)::INTEGER END AS review_after_delivery
            FROM p_orders CROSS JOIN analysis_config
            WHERE order_status='delivered'
              AND purchase_ts >= analysis_start AND purchase_ts < service_end
            ORDER BY purchase_ts, order_id
        """).df()
        expected = con.execute("""
            SELECT strftime(month, '%Y-%m') AS year_month,
              delivered_orders, known_date_orders, late_orders, reviewed_orders, low_review_orders
            FROM a_service_monthly ORDER BY month
        """).df().set_index("year_month")

    if orders.empty or orders.order_id.isna().any() or not orders.order_id.is_unique:
        raise AssertionError("Ожидается непустой факт с одной строкой на order_id")
    if not orders.is_late.dropna().isin([0, 1]).all():
        raise AssertionError("is_late должен содержать только 0, 1 или NULL")
    if not orders.review_score.dropna().between(1, 5).all():
        raise AssertionError("review_score должен быть от 1 до 5 или NULL")
    if not orders.is_low_score.isna().equals(orders.review_score.isna()):
        raise AssertionError("Отсутствующий отзыв должен сохранять NULL в is_low_score")
    if not orders.delivery_status.eq("Неизвестно").equals(orders.is_late.isna()):
        raise AssertionError("Неизвестная своевременность должна сохранять NULL")
    if not orders.is_low_score.dropna().isin([0, 1]).all():
        raise AssertionError("is_low_score должен содержать только 0, 1 или NULL")
    if not orders.review_after_delivery.dropna().isin([0, 1]).all():
        raise AssertionError("review_after_delivery должен содержать только 0, 1 или NULL")
    if (orders.delivery_days.dropna() < 0).any():
        raise AssertionError("Отрицательное время доставки не участвует в сроках")

    actual = orders.assign(year_month=orders.purchase_date.dt.strftime("%Y-%m")).groupby("year_month").agg(
        delivered_orders=("order_id", "size"),
        known_date_orders=("is_late", "count"), late_orders=("is_late", "sum"),
        reviewed_orders=("review_score", "count"), low_review_orders=("is_low_score", "sum"),
    ).sort_index()
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)

    dates = pd.date_range(start, end - timedelta(days=1))
    calendar = pd.DataFrame({
        "date": dates,
        "month_start": dates.to_period("M").to_timestamp(),
        "year": dates.year,
        "month_number": dates.month,
        "year_month": dates.strftime("%Y-%m"),
    })
    if not calendar.date.is_unique or len(calendar) != (end-start).days:
        raise AssertionError("Календарь должен иметь одну строку на каждый день периода")
    if not orders.purchase_date.isin(calendar.date).all():
        raise AssertionError("В календаре отсутствуют даты покупок")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "service_orders.csv": export_csv(orders, "service_orders.csv"),
        "service_calendar.csv": export_csv(calendar, "service_calendar.csv"),
    }
    totals = {column: int(actual[column].sum()) for column in actual.columns}
    manifest = {
        "source": "data/portfolio.duckdb:p_orders",
        "grain": "one row per delivered order_id",
        "analysis_start_inclusive": start.isoformat(),
        "service_end_exclusive": end.isoformat(),
        "observation_end_exclusive": observation_end.isoformat(),
        "source_status": "final snapshot; not an as-of reconstruction",
        "encoding": "UTF-8 with BOM",
        "null_encoding": "empty CSV cell; preserve as BLANK in Power BI",
        "totals": totals,
        "customer_states": int(orders.customer_state.nunique()),
        "reviews_after_delivery": int(orders.review_after_delivery.eq(1).sum()),
        "low_reviews_after_delivery": int((orders.review_after_delivery.eq(1)
                                           & orders.is_low_score.eq(1)).sum()),
        "recommended_relationship": "service_calendar[date] 1 -> * service_orders[purchase_date]; single direction",
        "exporter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "files": artifacts,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"service_orders.csv: {len(orders):,} заказов")
    print(f"service_calendar.csv: {len(calendar):,} дат")
    print(f"Сверка пяти показателей по месяцам с SQL: пройдена ({len(actual)} месяцев)")
    print(f"Папка: {OUTPUT}")


if __name__ == "__main__":
    main()
