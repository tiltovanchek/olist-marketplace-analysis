"""Data contracts and boundary cases for the portfolio SQL pipeline.

Run after ``python scripts/build_portfolio.py`` with:
    python -m unittest discover -s tests -v

Synthetic cases run entirely in memory. The published data is opened read-only.
"""

from decimal import Decimal
from pathlib import Path
import unittest

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "portfolio.duckdb"
MART_SQL = ROOT / "sql" / "06_portfolio_marts.sql"
METRICS_SQL = ROOT / "sql" / "07_product_metrics.sql"

RAW_COLUMNS = {
    "customers": "customer_id customer_unique_id customer_zip_code_prefix customer_city customer_state",
    "orders": "order_id customer_id order_status order_purchase_timestamp order_approved_at order_delivered_carrier_date order_delivered_customer_date order_estimated_delivery_date",
    "order_items": "order_id order_item_id product_id seller_id shipping_limit_date price freight_value",
    "order_payments": "order_id payment_sequential payment_type payment_installments payment_value",
    "order_reviews": "review_id order_id review_score review_comment_title review_comment_message review_creation_date review_answer_timestamp",
    "products": "product_id product_category_name product_name_lenght product_description_lenght product_photos_qty product_weight_g product_length_cm product_height_cm product_width_cm",
    "sellers": "seller_id seller_zip_code_prefix seller_city seller_state",
    "category_translation": "product_category_name product_category_name_english",
}


def insert_rows(con, table, rows):
    columns = RAW_COLUMNS[table].split()
    placeholders = ",".join("?" for _ in columns)
    con.executemany(
        f"INSERT INTO {table} VALUES ({placeholders})",
        [[row.get(column) for column in columns] for row in rows],
    )


def make_fixture():
    con = duckdb.connect(":memory:")
    for table, names in RAW_COLUMNS.items():
        columns = ",".join(f"{name} VARCHAR" for name in names.split())
        con.execute(f"CREATE TABLE {table} ({columns})")
    con.execute("""
        CREATE TABLE analysis_config AS SELECT
            DATE '2017-01-01' AS analysis_start,
            DATE '2018-09-01' AS observation_end,
            DATE '2018-07-01' AS service_end
    """)
    specifications = [
        ("fanout", "fanout_buyer", "2017-01-01 00:00:00", "2017-01-10 12:00:00", "2017-01-10"),
        ("same_day", "same_day_buyer", "2017-02-01 00:00:00", "2017-02-10 23:59:59", "2017-02-10"),
        ("unknown", "unknown_buyer", "2017-03-01 00:00:00", None, "2017-03-10"),
        ("future", "future_buyer", "2018-09-01 00:00:00", "2018-09-10", "2018-09-10"),
        ("mature_first", "mature_buyer", "2018-06-03 00:00:00", "2018-06-10", "2018-06-10"),
        ("mature_second", "mature_buyer", "2018-08-31 23:59:59", "2018-09-10", "2018-09-10"),
        ("immature_first", "immature_buyer", "2018-06-03 00:00:01", "2018-06-10", "2018-06-10"),
        ("edge_first", "edge_buyer", "2017-01-01 00:00:00", "2017-01-10", "2017-01-10"),
        ("edge_second", "edge_buyer", "2017-04-01 00:00:00", "2017-04-10", "2017-04-10"),
        ("pre_delivery_first", "pre_delivery_buyer", "2017-01-01 00:00:00", "2017-01-20", "2017-01-20"),
        ("pre_delivery_second", "pre_delivery_buyer", "2017-01-05 00:00:00", "2017-01-12", "2017-01-12"),
        ("split_first", "split_buyer", "2017-01-01 00:00:00", "2017-01-10", "2017-01-10"),
        ("split_second", "split_buyer", "2017-01-01 01:00:00", "2017-01-10", "2017-01-10"),
        ("split_third", "split_buyer", "2017-01-05 00:00:00", "2017-01-10", "2017-01-10"),
        ("august_first", "august_buyer", "2018-08-01 00:00:00", "2018-08-10", "2018-08-10"),
    ]
    insert_rows(con, "customers", [
        {"customer_id": order_id, "customer_unique_id": buyer, "customer_state": "SP"}
        for order_id, buyer, *_ in specifications
    ])
    insert_rows(con, "orders", [
        {"order_id": order_id, "customer_id": order_id, "order_status": "delivered",
         "order_purchase_timestamp": purchase, "order_delivered_customer_date": delivered,
         "order_estimated_delivery_date": estimated}
        for order_id, buyer, purchase, delivered, estimated in specifications
    ])
    insert_rows(con, "products", [{"product_id": "product", "product_category_name": "categoria"}])
    insert_rows(con, "sellers", [{"seller_id": "seller", "seller_state": "SP"}])
    insert_rows(con, "category_translation", [
        {"product_category_name": "categoria", "product_category_name_english": "category"}
    ])
    insert_rows(con, "order_items", [
        {"order_id": order_id, "order_item_id": "1", "product_id": "product",
         "seller_id": "seller", "price": "100.00", "freight_value": "10.00"}
        for order_id, *_ in specifications
    ] + [{"order_id": "fanout", "order_item_id": "2", "product_id": "product",
          "seller_id": "seller", "price": "50.00", "freight_value": "5.00"}])
    insert_rows(con, "order_payments", [
        {"order_id": "fanout", "payment_sequential": "1", "payment_value": "100.00"},
        {"order_id": "fanout", "payment_sequential": "2", "payment_value": "65.00"},
    ])
    insert_rows(con, "order_reviews", [
        {"review_id": "old", "order_id": "fanout", "review_score": "5",
         "review_creation_date": "2017-01-11", "review_answer_timestamp": "2017-01-12"},
        {"review_id": "new_a", "order_id": "fanout", "review_score": "3",
         "review_creation_date": "2017-01-12", "review_answer_timestamp": "2017-01-13",
         "review_comment_message": "short"},
        {"review_id": "new_b", "order_id": "fanout", "review_score": "2",
         "review_creation_date": "2017-01-12", "review_answer_timestamp": "2017-01-14",
         "review_comment_message": "latest response"},
    ])
    con.execute(MART_SQL.read_text(encoding="utf-8"))
    con.execute(METRICS_SQL.read_text(encoding="utf-8"))
    return con


class SyntheticSqlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = make_fixture()

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def test_joins_do_not_multiply_money_and_review_is_deduplicated(self):
        actual = self.con.execute("""
            SELECT items_count, items_gmv, freight_total, payment_total, review_score
            FROM p_orders WHERE order_id = 'fanout'
        """).fetchone()
        self.assertEqual(actual, (2, Decimal("150.00"), Decimal("15.00"), Decimal("165.00"), 2))

    def test_deadline_uses_calendar_date(self):
        self.assertIs(self.con.execute(
            "SELECT is_late FROM p_orders WHERE order_id = 'same_day'"
        ).fetchone()[0], False)

    def test_missing_delivery_date_stays_unknown(self):
        self.assertIsNone(self.con.execute(
            "SELECT is_late FROM p_orders WHERE order_id = 'unknown'"
        ).fetchone()[0])

    def test_observation_end_is_exclusive(self):
        for table in ("p_orders", "p_items"):
            self.assertEqual(self.con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE order_id = 'future'"
            ).fetchone()[0], 0)

    def test_exactly_mature_customer_is_eligible(self):
        self.assertEqual(self.con.execute("""
            SELECT repeat_90d_eligible, repeat_90d
            FROM p_customers WHERE customer_unique_id = 'mature_buyer'
        """).fetchone(), (True, True))

    def test_one_second_short_of_maturity_stays_unknown(self):
        self.assertEqual(self.con.execute("""
            SELECT repeat_90d_eligible, repeat_90d
            FROM p_customers WHERE customer_unique_id = 'immature_buyer'
        """).fetchone(), (False, None))

    def test_purchase_at_exactly_90_days_is_outside_repeat_window(self):
        self.assertEqual(self.con.execute("""
            SELECT repeat_90d_eligible, repeat_90d
            FROM p_customers WHERE customer_unique_id = 'edge_buyer'
        """).fetchone(), (True, False))

    def test_repeat_before_delivery_does_not_measure_delivery_experience(self):
        self.assertEqual(self.con.execute("""
            SELECT repeat_90d, repeat_after_delivery_90d_eligible,
                   repeat_after_delivery_90d
            FROM p_customers WHERE customer_unique_id = 'pre_delivery_buyer'
        """).fetchone(), (True, True, False))

    def test_delivery_and_purchase_windows_are_independent(self):
        self.assertEqual(self.con.execute("""
            SELECT repeat_90d, repeat_after_delivery_90d
            FROM p_customers WHERE customer_unique_id = 'edge_buyer'
        """).fetchone(), (False, True))

    def test_excluding_first_day_still_finds_a_third_order(self):
        self.assertEqual(self.con.execute("""
            SELECT repeat_within_1d, repeat_after_1d_90d
            FROM p_customers WHERE customer_unique_id = 'split_buyer'
        """).fetchone(), (True, True))

    def test_unobserved_retention_month_is_null(self):
        self.assertEqual(self.con.execute("""
            SELECT is_mature, retained_buyers, retention_pct
            FROM a_retention
            WHERE cohort_month = DATE '2018-08-01' AND month_age = 1
        """).fetchone(), (False, None, None))
        self.assertEqual(self.con.execute("""
            SELECT is_mature, retained_buyers, retention_pct
            FROM a_retention
            WHERE cohort_month = DATE '2018-08-01' AND month_age = 0
        """).fetchone(), (True, 1, 100.0))


@unittest.skipUnless(DB_PATH.exists(), "Build data/portfolio.duckdb first")
class PublishedDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.con = duckdb.connect(str(DB_PATH), read_only=True)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()

    def test_mart_grains(self):
        queries = {
            "orders": "SELECT COUNT(*) - COUNT(DISTINCT order_id) FROM p_orders",
            "items": "SELECT COUNT(*) - COUNT(DISTINCT (order_id, order_item_id)) FROM p_items",
            "buyers": "SELECT COUNT(*) - COUNT(DISTINCT customer_unique_id) FROM p_customers",
        }
        for grain, query in queries.items():
            with self.subTest(grain=grain):
                self.assertEqual(self.con.execute(query).fetchone()[0], 0)

    def test_missing_dates_never_become_on_time(self):
        self.assertEqual(self.con.execute("""
            SELECT COUNT(*) FROM p_orders
            WHERE (actual_delivery_ts IS NULL OR estimated_delivery_date IS NULL)
              AND is_late IS NOT NULL
        """).fetchone()[0], 0)

    def test_money_matches_raw_items_without_fanout(self):
        self.assertEqual(self.con.execute("""
            WITH raw_total AS (
                SELECT SUM(TRY_CAST(i.price AS DECIMAL(18,2))) AS gmv,
                       SUM(TRY_CAST(i.freight_value AS DECIMAL(18,2))) AS freight,
                       COUNT(*) AS items
                FROM order_items i JOIN p_orders p USING (order_id)
            ), mart_total AS (
                SELECT SUM(items_gmv) AS gmv, SUM(freight_total) AS freight,
                       SUM(items_count) AS items FROM p_orders
            )
            SELECT r.gmv = m.gmv AND r.freight = m.freight AND r.items = m.items
            FROM raw_total r CROSS JOIN mart_total m
        """).fetchone()[0], True)

    def test_payment_total_matches_raw(self):
        self.assertEqual(self.con.execute("""
            SELECT
                (SELECT SUM(TRY_CAST(payment_value AS DECIMAL(18,2)))
                 FROM order_payments JOIN p_orders USING (order_id))
                = (SELECT SUM(payment_total) FROM p_orders)
        """).fetchone()[0], True)

    def test_late_flag_matches_date_rule(self):
        self.assertEqual(self.con.execute("""
            SELECT COUNT(*) FROM p_orders
            WHERE is_late IS DISTINCT FROM CASE
                WHEN actual_delivery_ts IS NULL OR estimated_delivery_date IS NULL THEN NULL
                ELSE CAST(actual_delivery_ts AS DATE) > CAST(estimated_delivery_date AS DATE)
            END
        """).fetchone()[0], 0)

    def test_immature_buyers_do_not_receive_repeat_flags(self):
        self.assertEqual(self.con.execute("""
            SELECT COUNT(*) FROM p_customers
            WHERE (NOT repeat_90d_eligible AND repeat_90d IS NOT NULL)
               OR (NOT repeat_after_delivery_90d_eligible
                   AND repeat_after_delivery_90d IS NOT NULL)
        """).fetchone()[0], 0)

    def test_repeat_eligibility_uses_explicit_cutoff(self):
        self.assertEqual(self.con.execute("""
            SELECT COUNT(*) FROM p_customers CROSS JOIN analysis_config
            WHERE repeat_90d_eligible IS DISTINCT FROM
                  (first_purchase_ts + INTERVAL '90 days' <= observation_end)
        """).fetchone()[0], 0)

    def test_monthly_totals_reconcile_to_orders(self):
        self.assertEqual(self.con.execute("""
            WITH expected AS (
                SELECT COUNT(*) AS orders_count,
                       COUNT(*) FILTER (WHERE order_status = 'delivered') AS delivered_orders,
                       SUM(items_gmv) FILTER (WHERE order_status = 'delivered') AS gmv
                FROM p_orders CROSS JOIN analysis_config
                WHERE purchase_ts >= analysis_start
            ), actual AS (
                SELECT SUM(orders_count) AS orders_count,
                       SUM(delivered_orders) AS delivered_orders,
                       SUM(delivered_gmv) AS gmv FROM a_monthly
            )
            SELECT e.orders_count = a.orders_count
               AND e.delivered_orders = a.delivered_orders AND e.gmv = a.gmv
            FROM expected e CROSS JOIN actual a
        """).fetchone()[0], True)

    def test_repeat_denominator_only_contains_mature_buyers(self):
        self.assertEqual(self.con.execute("""
            SELECT (SELECT SUM(eligible_buyers) FROM a_repeat90)
                 = (SELECT COUNT(*) FROM p_customers CROSS JOIN analysis_config
                    WHERE first_purchase_ts >= analysis_start AND repeat_90d_eligible)
        """).fetchone()[0], True)

    def test_retention_censoring_and_cohort_size(self):
        self.assertEqual(self.con.execute("""
            SELECT COUNT(*) FROM a_retention
            WHERE (NOT is_mature AND (retained_buyers IS NOT NULL OR retention_pct IS NOT NULL))
               OR (is_mature AND (retained_buyers IS NULL OR retention_pct IS NULL))
               OR retained_buyers > cohort_buyers OR retained_buyers < 0
               OR (is_mature AND month_age = 0 AND retention_pct <> 100)
        """).fetchone()[0], 0)

    def test_category_gmv_is_additive(self):
        self.assertEqual(self.con.execute("""
            SELECT (SELECT SUM(gmv) FROM a_categories)
                 = (SELECT SUM(delivered_gmv) FROM a_monthly)
        """).fetchone()[0], True)


if __name__ == "__main__":
    unittest.main()
