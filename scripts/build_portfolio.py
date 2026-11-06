"""Build the portfolio analysis from the original CSVs; never edits olist.duckdb.

Run from any working directory: python /path/to/scripts/build_portfolio.py
All report numbers and notebook outputs are produced by this script.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import redirect_stdout
import hashlib
import io
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
TABLES = REPORTS / "tables"
FIGURES = REPORTS / "figures"
DB = DATA / "portfolio.duckdb"
CONFIG = {
    "analysis_start": "2017-01-01",
    "observation_end": "2018-09-01",
    "service_end": "2018-07-01",
}
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
SEED = 42


def scalar(con, query):
    return con.execute(query).fetchone()[0]


def validate_data(con):
    """Hard invariants fail the build; observed missingness is reported separately."""
    checks = []

    def equal(name, actual, expected=0):
        passed = bool(actual == expected)
        checks.append({"check": name, "actual": str(actual),
                       "expected": str(expected), "passed": passed})

    for table, key in [("orders", "order_id"), ("customers", "customer_id"),
                       ("products", "product_id"), ("sellers", "seller_id"),
                       ("p_orders", "order_id"), ("p_customers", "customer_unique_id")]:
        equal(f"{table}: unique non-null {key}", scalar(con,
              f"SELECT count(*)-count(DISTINCT {key}) FROM {table}"))
    equal("items: unique order/item", scalar(con, """
        SELECT count(*) FROM (SELECT order_id, order_item_id FROM order_items
        GROUP BY ALL HAVING count(*)>1 OR order_id IS NULL OR order_item_id IS NULL)
    """))
    equal("order -> customer integrity", scalar(con, """
        SELECT count(*) FROM orders o LEFT JOIN customers c USING(customer_id)
        WHERE c.customer_id IS NULL
    """))
    for table in ["order_items", "order_payments", "order_reviews"]:
        equal(f"{table} -> order integrity", scalar(con, f"""
            SELECT count(*) FROM {table} i LEFT JOIN orders o USING(order_id)
            WHERE o.order_id IS NULL
        """))
    for table, field in [("order_items", "price"), ("order_items", "freight_value"),
                         ("order_payments", "payment_value")]:
        equal(f"{table}.{field}: valid nonnegative money", scalar(con, f"""
            SELECT count(*) FROM {table}
            WHERE try_cast({field} AS DECIMAL(18,2)) IS NULL
               OR try_cast({field} AS DECIMAL(18,2))<0
        """))
    equal("orders: valid purchase timestamp", scalar(con, """
        SELECT count(*) FROM orders
        WHERE try_cast(order_purchase_timestamp AS TIMESTAMP) IS NULL
    """))
    equal("review scores in 1..5", scalar(con, """
        SELECT count(*) FROM order_reviews
        WHERE try_cast(review_score AS INTEGER) IS NULL
           OR try_cast(review_score AS INTEGER) NOT BETWEEN 1 AND 5
    """))
    equal("p_orders covers configured source population", scalar(con, "SELECT count(*) FROM p_orders"),
          scalar(con, """SELECT count(*) FROM orders CROSS JOIN analysis_config
          WHERE try_cast(order_purchase_timestamp AS TIMESTAMP)<observation_end"""))
    equal("GMV reconciles without join multiplication", scalar(con, """
        SELECT coalesce((SELECT sum(items_gmv) FROM p_orders),0)
          - (SELECT sum(try_cast(price AS DECIMAL(18,2))) FROM order_items
             SEMI JOIN p_orders USING(order_id))
    """))
    equal("payments reconcile without join multiplication", scalar(con, """
        SELECT coalesce((SELECT sum(payment_total) FROM p_orders),0)
          - (SELECT sum(try_cast(payment_value AS DECIMAL(18,2))) FROM order_payments
             SEMI JOIN p_orders USING(order_id))
    """))
    equal("late flag preserves unknown dates", scalar(con, """
        SELECT count(*) FROM p_orders WHERE is_late IS DISTINCT FROM CASE
        WHEN actual_delivery_ts IS NULL OR estimated_delivery_date IS NULL THEN NULL
        ELSE actual_delivery_ts::DATE>estimated_delivery_date::DATE END
    """))
    (REPORTS / "data_quality.json").write_text(
        json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [c["check"] for c in checks if not c["passed"]]
    if failures:
        raise AssertionError("Data quality failed: " + "; ".join(failures))
    return checks


def build_database():
    missing = [name for name in FILES.values() if not (DATA / name).is_file()]
    if missing:
        raise FileNotFoundError("Download the Olist CSVs into data/: " + ", ".join(missing))
    temporary = DATA / "portfolio.build.duckdb"
    with duckdb.connect(str(temporary)) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            for table, name in FILES.items():
                con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM "
                            "read_csv_auto(?, header=true, all_varchar=true)", [str(DATA / name)])
            con.execute("CREATE OR REPLACE TABLE analysis_config AS SELECT "
                        "?::DATE analysis_start, ?::DATE observation_end, ?::DATE service_end",
                        list(CONFIG.values()))
            for filename in ["06_portfolio_marts.sql", "07_product_metrics.sql"]:
                con.execute((ROOT / "sql" / filename).read_text(encoding="utf-8"))
            checks = validate_data(con)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    temporary.replace(DB)
    return checks


def wilson(successes, total):
    if not total:
        return [None, None]
    p, z = successes / total, norm.ppf(0.975)
    denominator = 1 + z*z / total
    center = (p + z*z / (2*total)) / denominator
    half = z * np.sqrt(p*(1-p)/total + z*z/(4*total*total)) / denominator
    return [float(center-half), float(center+half)]


def cluster_bootstrap(frame, group="is_late", outcome="low_score", iterations=2000):
    """Percentile CI; resample buyers and retain every order in each sampled cluster."""
    frame = frame[["customer_unique_id", group, outcome]].dropna().copy()
    frame[group] = frame[group].astype(bool)
    frame[outcome] = frame[outcome].astype(float)
    for exposed, label in [(True, "late"), (False, "on_time")]:
        frame[f"{label}_n"] = (frame[group] == exposed).astype(float)
        frame[f"{label}_y"] = frame[outcome] * frame[f"{label}_n"]
    columns = ["late_y", "late_n", "on_time_y", "on_time_n"]
    clusters = frame.groupby("customer_unique_id", sort=True)[columns].sum().to_numpy()
    if len(clusters) == 0:
        raise ValueError("No eligible buyers for bootstrap")
    totals = clusters.sum(axis=0)
    if totals[1] == 0 or totals[3] == 0:
        raise ValueError("Both exposure groups are required")
    observed = totals[0]/totals[1] - totals[2]/totals[3]
    rng = np.random.default_rng(SEED)
    differences = []
    # Batched indices keep memory bounded; this is ordinary cluster resampling.
    for start in range(0, iterations, 20):
        indices = rng.integers(0, len(clusters), size=(min(20, iterations-start), len(clusters)))
        samples = clusters[indices].sum(axis=1)
        valid = (samples[:, 1]>0) & (samples[:, 3]>0)
        differences.extend((samples[valid, 0]/samples[valid, 1]
                            - samples[valid, 2]/samples[valid, 3]).tolist())
    ci = np.quantile(differences, [0.025, 0.975])
    return {"difference_pp": float(100*observed),
            "ci95_pp": [float(v*100) for v in ci],
            "buyers": len(clusters), "orders": len(frame),
            "late_n": int(totals[1]), "on_time_n": int(totals[3]),
            "late_rate": float(totals[0]/totals[1]),
            "on_time_rate": float(totals[2]/totals[3]),
            "iterations": iterations, "seed": SEED}


def experiment_size(baseline, mde=0.005):
    """Two-sided normal approximation, independent buyers, 1:1 assignment."""
    alternative = baseline + mde
    pooled = (baseline + alternative)/2
    n = ((norm.ppf(.975)*np.sqrt(2*pooled*(1-pooled))
          + norm.ppf(.8)*np.sqrt(baseline*(1-baseline)
                                     + alternative*(1-alternative))) / mde)**2
    return {"baseline_proxy": float(baseline), "absolute_mde": mde,
            "alpha": .05, "power": .8, "buyers_per_arm": int(np.ceil(n)),
            "total_buyers": 2*int(np.ceil(n)),
            "note": "Приближение для планирования по покупателям с доставленным заказом и полным окном после получения. Перед тестом исходную долю нужно заново оценить на наблюдательной группе повышенного риска по правилам будущего теста, без исключений по последующим событиям."}


def compute_statistics(con, iterations):
    service = con.execute("""
        SELECT o.* FROM p_orders o CROSS JOIN analysis_config c
        WHERE order_status='delivered' AND purchase_ts>=analysis_start
          AND purchase_ts<service_end
    """).df()
    reviewed = service.loc[service.is_late.notna() & service.review_score.notna()].copy()
    reviewed["low_score"] = reviewed.review_score.le(2)
    main = cluster_bootstrap(reviewed, iterations=iterations)
    after = reviewed.loc[reviewed.review_answer_ts.ge(reviewed.actual_delivery_ts)].copy()
    sensitivity = cluster_bootstrap(after, iterations=iterations)
    # Compare within month x state. Weights are pooled reviewed-order shares.
    # This standardization is descriptive and does not remove unobserved confounding.
    reviewed["month"] = reviewed.purchase_ts.dt.to_period("M").astype(str)
    strata = reviewed.groupby(["month", "customer_state", "is_late"], observed=True).agg(
        n=("order_id", "size"), low=("low_score", "sum")).reset_index()
    rates = strata.pivot(index=["month", "customer_state"], columns="is_late", values="low")
    counts = strata.pivot(index=["month", "customer_state"], columns="is_late", values="n")
    comparable = counts.dropna().index
    counts = counts.loc[comparable]
    rates = rates.loc[comparable] / counts
    weights = counts.sum(axis=1) / counts.sum().sum()
    adjusted_pp = float(((rates[True]-rates[False])*weights).sum()*100)
    strata.to_csv(TABLES / "review_month_state.csv", index=False)
    stats = {"review_low_score": main, "review_after_delivery": sensitivity,
             "month_state_standardized_difference_pp": adjusted_pp,
             "standardized_strata": len(comparable),
             "standardized_review_coverage": float(counts.sum().sum()/len(reviewed)),
             "service_orders": len(service),
             "service_unknown_delivery_dates": int(service.is_late.isna().sum()),
             "service_missing_reviews": int(service.review_score.isna().sum()),
             "reviews_before_delivery": int((reviewed.review_answer_ts<reviewed.actual_delivery_ts).sum())}
    return stats


def style():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titleweight": "bold", "figure.facecolor": "#fbfcfe",
                         "axes.facecolor": "#fbfcfe", "savefig.facecolor": "#fbfcfe",
                         "axes.labelcolor": "#334155", "text.color": "#0f172a",
                         "axes.grid": True, "grid.alpha": .16,
                         "axes.axisbelow": True})


def save_figure(fig, name):
    fig.savefig(FIGURES / name, dpi=170, bbox_inches="tight")
    plt.close(fig)


def review_figure(main, after_delivery):
    """Show observed low-score rates and the already calculated difference intervals."""
    fig, axes = plt.subplots(1, 2, figsize=(11.8, 5.8), sharey=True)
    for ax, result, caption in zip(
        axes, (main, after_delivery), ("Все выбранные отзывы", "Отзывы после получения")
    ):
        rates = [result["on_time_rate"] * 100, result["late_rate"] * 100]
        counts = [result["on_time_n"], result["late_n"]]
        ax.bar(["Вовремя", "С опозданием"], rates,
               color=["#0f766e", "#c2410c"], width=.62)
        ax.set(title=caption, ylim=(0, 80))
        ax.grid(axis="x", visible=False)
        for index, (rate, count) in enumerate(zip(rates, counts)):
            percent = f"{rate:.2f}".replace(".", ",")
            orders = f"{count:,}".replace(",", " ")
            ax.text(index, rate + 2, f"{percent}%\n{orders} отзывов",
                    ha="center", va="bottom", fontsize=10)
        diff = f"{result['difference_pp']:.2f}".replace(".", ",")
        lo, hi = (f"{value:.2f}".replace(".", ",") for value in result["ci95_pp"])
        ax.text(.5, -.17, f"Разница долей: +{diff} п.п.\n95%-й ДИ: [{lo}; {hi}]",
                transform=ax.transAxes, ha="center", va="top", fontsize=10)
    axes[0].set_ylabel("Низкие оценки (1–2), %")
    fig.suptitle("Доля низких оценок и сроки доставки", fontsize=14, fontweight="bold", y=.98)
    fig.text(.5, .14, "Разница = с опозданием минус вовремя; ДИ — доверительный интервал в процентных пунктах.",
             ha="center", fontsize=9)
    fig.text(.5, .08, "Показаны заказы с выбранным отзывом и известной своевременностью доставки.",
             ha="center", fontsize=9)
    fig.text(.5, .035, "Отбор по времени отправки отзыва меняет состав групп; сравнение не оценивает причинный эффект.",
             ha="center", fontsize=9)
    fig.subplots_adjust(left=.08, right=.98, bottom=.36, top=.83, wspace=.18)
    return fig


def markdown_table(df, digits=2):
    def cell(value):
        if pd.isna(value):
            return "—"
        if isinstance(value, (float, np.floating)):
            return f"{value:,.{digits}f}"
        if isinstance(value, pd.Timestamp):
            return value.strftime("%Y-%m")
        return str(value).replace("|", "/").replace("\n", " ")
    return "\n".join(["| " + " | ".join(map(str, df.columns)) + " |",
                       "| " + " | ".join(["---"]*len(df.columns)) + " |"]
                      + ["| " + " | ".join(cell(v) for v in row) + " |"
                         for row in df.itertuples(index=False, name=None)])


def number(value, digits=0):
    return f"{value:,.{digits}f}".replace(",", " ")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    args = parser.parse_args()
    if args.bootstrap_iterations<100:
        parser.error("At least 100 bootstrap iterations are required")
    for directory in [REPORTS, TABLES, FIGURES]:
        directory.mkdir(parents=True, exist_ok=True)
    checks = build_database()
    print(f"Database built; {len(checks)} data quality checks passed.")
    with duckdb.connect(str(DB), read_only=True) as con:
        tables = {}
        names = con.execute("SELECT table_name FROM information_schema.tables "
                            "WHERE starts_with(table_name, 'a_') ORDER BY table_name").fetchall()
        for (name,) in names:
            frame = con.execute(f"SELECT * FROM {name}").df()
            frame.to_csv(TABLES / f"{name}.csv", index=False)
            tables[name] = frame
        stats = compute_statistics(con, args.bootstrap_iterations)
        write_outputs(con, tables, stats, checks)
        source_rows = {name: scalar(con, f"SELECT count(*) FROM {table}")
                       for table, name in FILES.items()}
    manifest = {"config": CONFIG, "python": platform.python_version(),
                "packages": {"duckdb": duckdb.__version__, "pandas": pd.__version__,
                             "numpy": np.__version__, "matplotlib": matplotlib.__version__,
                             "scipy": importlib.metadata.version("scipy"),
                             "ipython": importlib.metadata.version("ipython")},
                "bootstrap_iterations": args.bootstrap_iterations, "seed": SEED,
                "sources": {name: {"sha256": hashlib.sha256((DATA/name).read_bytes()).hexdigest(),
                                   "bytes": (DATA/name).stat().st_size,
                                   "rows": source_rows[name]} for name in FILES.values()},
                "sql_sha256": {name: hashlib.sha256((ROOT/"sql"/name).read_bytes()).hexdigest()
                               for name in ["06_portfolio_marts.sql", "07_product_metrics.sql"]},
                "code_sha256": {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                                for name in ["scripts/build_portfolio.py", "requirements.txt"]}}
    (REPORTS / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    previous_directory = Path.cwd()
    try:
        os.chdir(ROOT)
        make_notebook()
    finally:
        os.chdir(previous_directory)
    print(f"Report: {REPORTS / 'executive_summary.md'}")
    print(f"Notebook: {ROOT / 'notebooks' / '02_product_analysis.ipynb'}")


# The rendering functions below consume the SQL tables, with no manual report numbers.
def write_outputs(con, tables, stats, checks):
    style()
    monthly = tables["a_monthly"].sort_values("month")
    repeat = tables["a_repeat90"].sort_values("cohort_month")
    retention = tables["a_retention"]
    service = tables["a_service_monthly"].sort_values("month")
    segments = tables["a_service_segments"].sort_values("late_orders", ascending=False)
    categories = tables["a_categories"].sort_values("gmv", ascending=False)
    reviews = tables["a_review_delivery"].sort_values("is_late")

    growth = con.execute("""
        SELECT CASE WHEN purchase_ts<DATE '2018-01-01' THEN '2017 H1' ELSE '2018 H1' END period,
          count(*) orders, count(DISTINCT customer_unique_id) buyers,
          sum(items_gmv)::DOUBLE gmv,
          count(*)::DOUBLE/count(DISTINCT customer_unique_id) orders_per_buyer,
          sum(items_gmv)::DOUBLE/count(*) aov
        FROM p_orders WHERE order_status='delivered' AND
          ((purchase_ts>=DATE '2017-01-01' AND purchase_ts<DATE '2017-07-01')
          OR (purchase_ts>=DATE '2018-01-01' AND purchase_ts<DATE '2018-07-01'))
        GROUP BY 1 ORDER BY 1
    """).df()
    growth.to_csv(TABLES / "growth_decomposition.csv", index=False)
    customers = con.execute("""
        SELECT p.* FROM p_customers p CROSS JOIN analysis_config
        WHERE first_purchase_ts>=analysis_start
    """).df()
    mature = customers.loc[customers.repeat_90d_eligible.fillna(False)]
    repeat_n, repeat_y = len(mature), int(mature.repeat_90d.sum())
    after1d_y = int(mature.repeat_after_1d_90d.sum())
    post = customers.loc[customers.repeat_after_delivery_90d_eligible.fillna(False)
                         & customers.first_purchase_ts.lt(pd.Timestamp(CONFIG["service_end"]))]
    post_y, post_n = int(post.repeat_after_delivery_90d.sum()), len(post)
    post_groups = []
    for flag, group in post.dropna(subset=["is_late"]).groupby("is_late"):
        y, n = int(group.repeat_after_delivery_90d.sum()), len(group)
        lo, hi = wilson(y, n)
        post_groups.append({"is_late": bool(flag), "eligible_buyers": n,
                            "repeated_buyers": y, "repeat_pct": 100*y/n,
                            "ci95_low_pct": lo*100, "ci95_high_pct": hi*100})
    pd.DataFrame(post_groups).to_csv(TABLES / "repeat_after_delivery_by_lateness.csv", index=False)
    earlier = customers.loc[(customers.first_purchase_ts + pd.Timedelta(days=90))
                            .le(pd.Timestamp("2018-08-01"))]
    earlier_y = int(earlier.repeat_90d.sum())
    repeat_summary = {
        "eligible_buyers": repeat_n, "repeated_buyers": repeat_y,
        "repeat_rate": repeat_y/repeat_n, "ci95": wilson(repeat_y, repeat_n),
        "repeat_after_1d_buyers": after1d_y, "repeat_after_1d_rate": after1d_y/repeat_n,
        "same_day_second_buyers": int(mature.repeat_within_1d.sum()),
        "earlier_cutoff": "2018-08-01", "earlier_eligible_buyers": len(earlier),
        "earlier_repeat_rate": earlier_y/len(earlier),
        "postdelivery_eligible_buyers": post_n, "postdelivery_repeated_buyers": post_y,
        "postdelivery_rate": post_y/post_n,
    }
    stats["repeat90"] = repeat_summary
    stats["experiment"] = experiment_size(post_y/post_n)
    stats["data_quality_checks"] = len(checks)
    (REPORTS / "statistics.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    fig, axes = plt.subplots(2, 1, figsize=(11, 6.8), sharex=True)
    axes[0].plot(monthly.month, monthly.delivered_gmv/1e6, color="#0f766e", lw=2.4, marker="o", ms=4)
    axes[0].set(ylabel="GMV, млн R$", title="Рост объёма и покупателей: наблюдаемые доставленные заказы")
    axes[1].bar(monthly.month, monthly.delivered_orders, width=21, color="#38bdf8")
    axes[1].set(ylabel="Доставленные заказы", xlabel="Месяц покупки")
    for ax in axes:
        ax.axvspan(pd.Timestamp("2018-07-01"), pd.Timestamp("2018-09-01"), color="#f59e0b", alpha=.12)
    axes[0].text(.99, .05, "Июль–август: осторожно с полнотой исполнения", ha="right",
                 transform=axes[0].transAxes, fontsize=9, color="#92400e")
    fig.autofmt_xdate()
    fig.tight_layout()
    save_figure(fig, "01_growth.png")

    mature_cohorts = repeat.loc[repeat.eligible_buyers.gt(0)].copy()
    bounds = np.array([wilson(y, n) for y, n in zip(mature_cohorts.repeat_buyers,
                                                   mature_cohorts.eligible_buyers)])*100
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.8), sharex=True,
                             gridspec_kw={"height_ratios": [2, 1]})
    axes[0].errorbar(mature_cohorts.cohort_month, mature_cohorts.repeat_rate_pct,
                    yerr=np.array([mature_cohorts.repeat_rate_pct-bounds[:,0],
                                   bounds[:,1]-mature_cohorts.repeat_rate_pct]),
                    fmt="o-", color="#0f766e", capsize=3, label="Повтор в первые 90 суток, 95%-й интервал Уилсона")
    axes[0].plot(mature_cohorts.cohort_month, mature_cohorts.repeat_after_1d_rate_pct,
                 "s--", color="#c2410c", label="Повтор после первых 24 часов")
    axes[0].set(ylabel="Доля покупателей, %", title="Повторные покупки: только покупатели с полным окном")
    axes[0].legend(loc="upper left", fontsize=9)
    axes[0].set_ylim(bottom=0)
    axes[1].bar(mature_cohorts.cohort_month, mature_cohorts.eligible_buyers, width=22, color="#cbd5e1")
    axes[1].set(ylabel="Зрелые покупатели", xlabel="Месяц первой наблюдаемой доставленной покупки")
    fig.autofmt_xdate()
    fig.tight_layout()
    save_figure(fig, "02_repeat90.png")

    pivot = retention.pivot(index="cohort_month", columns="month_age", values="retention_pct").sort_index()
    matrix = pivot.to_numpy(dtype=float)
    fig, ax = plt.subplots(figsize=(10, 8))
    # M0 is 100% by definition; cap the scale for visibility of later-month activity.
    cmap = plt.get_cmap("YlGnBu").copy()
    cmap.set_bad("#e2e8f0")
    plot = ax.imshow(np.ma.masked_invalid(matrix), aspect="auto", cmap=cmap, vmin=0, vmax=3)
    ax.set_xticks(range(len(pivot.columns)), [f"M{v}" for v in pivot.columns])
    ax.set_yticks(range(len(pivot)), [pd.Timestamp(v).strftime("%Y-%m") for v in pivot.index])
    for i in range(len(pivot)):
        for j in range(len(pivot.columns)):
            val = matrix[i,j]
            ax.text(j, i, "—" if np.isnan(val) else f"{val:.1f}%", ha="center", va="center",
                    color="white" if not np.isnan(val) and val>=1.8 else "#0f172a", fontsize=8)
    ax.set(title="Календарный retention: M0 = 100%; серые клетки ещё не наблюдались",
           xlabel="Месяц с первой наблюдаемой покупки", ylabel="Когорта")
    ax.grid(False)
    fig.colorbar(plot, ax=ax, label="Retention, % (шкала ограничена 3%)", fraction=.03)
    fig.tight_layout()
    save_figure(fig, "03_retention.png")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5.3))
    axes[0].plot(service.month, service.late_rate_pct, "o-", color="#c2410c")
    axes[0].set(title="Опоздания по месяцу покупки", ylabel="Поздние / с известными датами, %", ylim=(0, None))
    ranked = segments.head(10).sort_values("late_orders")
    axes[1].barh(ranked.customer_state, ranked.late_orders, color="#0f766e")
    for i, row in enumerate(ranked.itertuples()):
        axes[1].text(row.late_orders+12, i, f"{row.late_rate_pct:.1f}%", va="center", fontsize=9)
    axes[1].set(title="Где сосредоточено число опозданий", xlabel="Поздние заказы; подпись = доля в штате")
    axes[1].set_xlim(right=max(ranked.late_orders)*1.23)
    fig.autofmt_xdate(rotation=45)
    fig.suptitle("Качество доставки · покупки с января 2017 по июнь 2018", y=1.02, fontweight="bold")
    fig.tight_layout()
    save_figure(fig, "04_delivery.png")

    main, sensitivity = stats["review_low_score"], stats["review_after_delivery"]
    fig = review_figure(main, sensitivity)
    save_figure(fig, "05_reviews.png")

    top = categories.head(10).sort_values("gmv")
    fig, ax = plt.subplots(figsize=(10, 5.8))
    ax.barh(top.category_name, top.gmv/1e6, color="#0f766e")
    ax.set(xlabel="GMV товаров, млн R$", title="Категории с наибольшим GMV · январь 2017 — август 2018")
    fig.tight_layout()
    save_figure(fig, "06_categories.png")

    g0, g1 = growth.iloc[0], growth.iloc[1]
    gmv_growth = (g1.gmv/g0.gmv-1)*100
    buyer_growth = (g1.buyers/g0.buyers-1)*100
    order_growth = (g1.orders/g0.orders-1)*100
    aov_growth = (g1.aov/g0.aov-1)*100
    frequency_growth = (g1.orders_per_buyer/g0.orders_per_buyer-1)*100
    total_gmv = float(monthly.delivered_gmv.sum())
    total_orders = int(monthly.delivered_orders.sum())
    all_orders = int(monthly.orders_count.sum())
    late_n, known_n = int(service.late_orders.sum()), int(service.known_date_orders.sum())
    worst = service.loc[service.late_rate_pct.idxmax()]
    states = segments.head(3)
    state_share = states.late_orders.sum()/late_n*100
    source_orders = scalar(con, "SELECT count(*) FROM orders")
    excluded = scalar(con, "SELECT count(*) FROM orders") - scalar(con, "SELECT count(*) FROM p_orders")
    missing_payments = scalar(con, """SELECT count(*) FROM p_orders CROSS JOIN analysis_config
        WHERE order_status='delivered' AND purchase_ts>=analysis_start
        AND payment_total IS NULL""")
    payment_diagnostic = con.execute("""
        SELECT count(*) FILTER (WHERE payment_total IS NOT NULL) compared_orders,
          count(*) FILTER (WHERE abs(payment_total-items_gmv-freight_total)>0.01) difference_orders,
          sum(payment_total-items_gmv-freight_total)::DOUBLE net_difference_brl
        FROM p_orders CROSS JOIN analysis_config
        WHERE order_status='delivered' AND purchase_ts>=analysis_start
    """).df()
    payment_diagnostic.to_csv(TABLES / "payment_reconciliation.csv", index=False)
    payment_row = payment_diagnostic.iloc[0]
    ci = main["ci95_pp"]
    plan = stats["experiment"]
    late_pre_delivery = main["late_n"] - sensitivity["late_n"]
    summary_text = (
        f"В первом полугодии 2018 GMV доставленных товаров вырос на **{gmv_growth:.1f}%** к тому же "
        f"периоду 2017. Рост сопровождается увеличением числа покупателей на **{buyer_growth:.1f}%**. "
        f"Повторную покупку за 90 суток совершили **{100*repeat_y/repeat_n:.2f}%** "
        f"зрелых покупателей. У поздних заказов доля оценок 1–2 выше на **{main['difference_pp']:.1f} п.п.** "
        f"(95%-й доверительный интервал {ci[0]:.1f}–{ci[1]:.1f}); среди отзывов, отправленных после доставки, "
        f"разница уменьшается до **{sensitivity['difference_pp']:.1f} п.п.** "
        "Приоритет для проверки — работа с риском задержки и коммуникацией после покупки. "
        "Исторические данные показывают наблюдаемую связь; эффект улучшения нужно измерить экспериментом."
    )
    growth_view = growth.rename(columns={"period":"Период", "orders":"Заказы", "buyers":"Покупатели",
                                         "gmv":"GMV R$", "orders_per_buyer":"Заказов/покупатель", "aov":"AOV R$"})
    state_view = states[["customer_state","known_date_orders","late_orders","late_rate_pct"]].rename(
        columns={"customer_state":"Штат", "known_date_orders":"Заказы с датами", "late_orders":"Поздние", "late_rate_pct":"Поздние, %"})
    post_view = pd.DataFrame(post_groups).assign(
        is_late=lambda frame: frame["is_late"].map({False: "Вовремя", True: "С опозданием"})
    ).rename(columns={"is_late":"Первая доставка", "eligible_buyers":"Покупатели",
                    "repeated_buyers":"Повторили", "repeat_pct":"Повтор, %", "ci95_low_pct":"Нижняя граница интервала, %", "ci95_high_pct":"Верхняя граница интервала, %"})
    report = f"""# Olist: рост маркетплейса и качество покупательского опыта

{summary_text}

## Контекст решения

Цель анализа — определить направления улучшения покупательского опыта после заказа
и выбрать проверяемую продуктовую гипотезу на данных маркетплейса.
Выгрузка содержит {number(source_orders)} заказов. Основной бизнес-период — 01.01.2017–31.08.2018:
{number(all_orders)} заказов всех статусов, {number(total_orders)} доставленных, GMV **R$ {number(total_gmv, 2)}**.
GMV — стоимость товаров без доставки, не выручка платформы. Данные статусов — итоговый снимок.
Определения специальных терминов собраны в [словаре проекта](../docs/glossary.md).

## 1. Рост: больше покупателей, а не доказанное улучшение удержания

Сравниваем одинаковые полугодия, чтобы не принять разную длину периодов за рост:

{markdown_table(growth_view)}

GMV = уникальные покупатели × заказы на покупателя × AOV.
Изменения факторов этого произведения: покупатели {buyer_growth:+.1f}%, частота {frequency_growth:+.1f}%,
AOV {aov_growth:+.1f}%; всего заказов {order_growth:+.1f}%, GMV {gmv_growth:+.1f}%.
Это сравнение агрегатов, а не доказательство причин роста или эффективности привлечения.

![Помесячный рост](figures/01_growth.png)

Июль–август показаны для контекста, но близость к концу выгрузки ограничивает выводы об исполнении.
Статусы заказа не образуют событийную воронку. Конверсия из посещения в покупку здесь не наблюдается.

## 2. Повторные покупки: зрелые окна и проверка чувствительности

Первый заказ — первый **наблюдаемый доставленный** заказ покупателя во всей доступной истории.
Заказанные до 2017 товары участвуют в определении первой покупки, но их когорты исключены из итоговой метрики.
Повтор за первые 90 суток: **{number(repeat_y)} / {number(repeat_n)} = {100*repeat_y/repeat_n:.2f}%**,
95%-й доверительный интервал Уилсона **{100*repeat_summary['ci95'][0]:.2f}–{100*repeat_summary['ci95'][1]:.2f}%**.
Среди вторых заказов {number(repeat_summary['same_day_second_buyers'])} сделаны в первые 24 часа;
они могут отражать разбиение покупки и не обязательно означают возвращение после использования товара.
Если считать любое повторное событие только после первых 24 часов, результат —
**{number(after1d_y)} / {number(repeat_n)} = {100*after1d_y/repeat_n:.2f}%**.
Это изменение определения события, покупатели остаются в знаменателе.

При более раннем конце наблюдения 01.08.2018: **{repeat_summary['earlier_repeat_rate']*100:.2f}%**
на {number(len(earlier))} покупателях с полным окном. Состав когорт меняется; это проверка чувствительности к концу наблюдения,
а не сравнение одинаковых групп. В июне 2018 зрелой может быть только ранняя часть месячной когорты;
число вошедших покупателей показано под графиком.

![Повторные покупки](figures/02_repeat90.png)

Календарное удержание покупателей (retention) отвечает другому вопросу: был ли хотя бы один доставленный заказ в месяце M1–M6.
M0 = 100% по построению; неполные календарные месяцы оставлены пустыми, а не заменены нулями.

![Календарные когорты](figures/03_retention.png)

## 3. Доставка: приоритет по объёму проблемы

Для сервиса берём покупки до 01.07.2018: остаётся минимум 62 дня до конца окна.
Этот запас времени уменьшает риск неполного наблюдения доставки. В анализ входят заказы с конечным статусом «доставлен»;
полнота данных всё равно не гарантирована.
Поздних заказов **{number(late_n)} / {number(known_n)} = {100*late_n/known_n:.2f}%**.
В сервисном срезе {stats['service_unknown_delivery_dates']} заказов с неизвестной своевременностью;
они не входят в знаменатель. Самый высокий показатель по месяцу покупки:
**{worst['month']:%Y-%m} — {worst.late_rate_pct:.2f}%**.

{markdown_table(state_view)}

Три штата с наибольшим числом опозданий дают **{state_share:.1f}%** поздних заказов.
Порог для сравнения штатов — минимум 100 заказов с известными датами.
Это список для диагностики логистики; различия состава заказов не позволяют приписать проблему штату или продавцу.

![Доставка](figures/04_delivery.png)

## 4. Отзывы и повторные покупки: наблюдаемая связь и её ограничения

Доля оценок 1–2 среди оставивших отзыв: **{main['late_rate']*100:.2f}%** при опоздании
(n={number(main['late_n'])}) и **{main['on_time_rate']*100:.2f}%** при своевременной доставке
(n={number(main['on_time_n'])}). Разница **{main['difference_pp']:.2f} п.п.**,
95%-й доверительный интервал **[{ci[0]:.2f}; {ci[1]:.2f}]**.
Для оценки неопределённости многократно выбираем покупателей с возвращением и сохраняем все заказы каждого
выбранного покупателя — это кластерный бутстрап: {main['iterations']} повторений,
начальное значение генератора случайных чисел {SEED}, {number(main['buyers'])} покупателей.
Один выбранный отзыв на заказ; {number(stats['service_missing_reviews'])} заказов без оценки в сервисном срезе.

{number(stats['reviews_before_delivery'])} отзывов отправлены до доставки,
из них {number(late_pre_delivery)} относятся к поздним заказам.
Для отзывов, отправленных не раньше доставки, разница составляет **{sensitivity['difference_pp']:.2f} п.п.**,
95%-й доверительный интервал **[{sensitivity['ci95_pp'][0]:.2f}; {sensitivity['ci95_pp'][1]:.2f}]**.
Доли низких оценок в этом срезе — **{sensitivity['late_rate']*100:.2f}%** для заказов с опозданием
и **{sensitivity['on_time_rate']*100:.2f}%** для доставленных вовремя.
Фильтр оставляет **{100*sensitivity['late_n']/main['late_n']:.1f}%** отзывов о поздних заказах
и **{100*sensitivity['on_time_n']/main['on_time_n']:.1f}%** отзывов о своевременных заказах.
Время отправки отзыва существенно меняет состав сравниваемых групп. Это отдельный срез,
а не исправленный причинный эффект; большой исходный разрыв нельзя переносить на опыт после получения товара.
После стандартизации по месяцу покупки × штату разница **{stats['month_state_standardized_difference_pp']:.2f} п.п.**:
веса пропорциональны числу заказов с отзывом в сочетаниях месяца и штата, где представлены обе группы;
покрытие **{stats['standardized_review_coverage']*100:.1f}%**, сочетаний **{stats['standardized_strata']}**.
Для этой оценки интервал не рассчитан. Неучтённые факторы и различия в вероятности оставить отзыв остаются.

![Отзывы и опоздания](figures/05_reviews.png)

Повтор после доставки считается отдельно: любая последующая доставленная покупка строго после
первой доставки и до истечения 90 суток после неё, с полным окном наблюдения.
В когортах сервисного периода это **{number(post_y)} / {number(post_n)} = {post_y/post_n*100:.2f}%**.

{markdown_table(post_view)}

Эти группы сформированы по фактическому исходу доставки. Разницы не являются причинным эффектом.
Нельзя объяснять доставкой повтор, который произошёл раньше неё. Поля статусов и отзывов относятся
к итоговому снимку. Конец наблюдения ограничивает даты покупок и полноту окон;
историческое состояние системы на эту дату по этим полям восстановить нельзя.

## 5. Ассортимент как контекст, а не готовое решение

Топ-10 категорий дают **{categories.head(10).gmv_share_pct.sum():.1f}%** GMV.
В категорийном анализе деньги распределены по позициям; число заказов между категориями неаддитивно.
Рост категории сам по себе не доказывает, что в неё нужно инвестировать: нет маржи, затрат и экспозиции каталога.

![Категории](figures/06_categories.png)

## 6. Решения и следующий эксперимент

| Приоритет | Действие | На что опирается | Как проверить |
| --- | --- | --- | --- |
| 1 | Разобрать логистику в штатах с наибольшим числом поздних заказов и пиковые месяцы | Число и доля опозданий | Детализация по перевозчику, продавцу и маршруту, обещанные сроки и операционные причины |
| 2 | Проверить проактивное уведомление при риске задержки | Наблюдаемая связь опоздания и низкой оценки | Рандомизированный тест на уровне покупателя до уведомления |
| 3 | Исследовать разбиение покупок и паттерны повторного спроса | Чувствительность repeat90 к первым суткам | Идентификатор сессии, связь заказов, интервью и события использования |

Для будущего A/B-теста основная метрика — доля покупателей с повторной покупкой в фиксированном 150-дневном горизонте:
первый подходящий заказ доставлен в первые 60 суток, затем повтор в следующие 90 суток.
Все покупатели, назначенные в экспериментальные группы, остаются в знаменателе,
включая покупателей с недоставленным первым заказом: это анализ по первоначальному назначению (ITT).
Подробности, контрольные метрики и критерии анализа — в [дизайне эксперимента](../docs/experiment_design.md).

Исходная доля для приближённого планирования — **{post_y/post_n*100:.2f}%**.
Она рассчитана среди покупателей с доставленным заказом и полным 90-дневным окном после получения.
При минимальном изменении для расчёта мощности (MDE) **+0.5 п.п.**, уровне значимости 0.05,
мощности 80% и распределении 1:1 нормальное приближение даёт
**{number(plan['buyers_per_arm'])} покупателей на группу**, всего **{number(plan['total_buyers'])}**.
При такой исходной доле +0.5 п.п. соответствует **{100*plan['absolute_mde']/plan['baseline_proxy']:.1f}%**
относительного увеличения: тест рассчитан на обнаружение крупного относительного изменения.
Это иллюстрация расчёта чувствительности, не прогноз эффекта. До запуска нужно оценить исходную долю
на наблюдательной группе повышенного риска, собранной по правилам будущего теста,
без исключений по последующим событиям. Также нужны доступное число подходящих покупателей
и стоимость ожидания 150 дней.
Оценки риска должны использовать только данные, доступные на момент назначения варианта.

## Надёжность и воспроизведение

Сборка прошла **{len(checks)} проверок**; результаты — [data_quality.json](data_quality.json).
Число строк, уникальность заказов и денежные итоги сверяются с CSV;
тесты дополнительно проверяют размножение строк при JOIN, даты и границы окон.
Из витрины исключено **{excluded} заказов с датой покупки за пределами окна наблюдения**:
{pd.Timestamp(CONFIG['observation_end']):%d.%m.%Y} и позже.
В бизнес-периоде {missing_payments} доставленных заказов без платежных строк; GMV считается по позициям.
Платежи не принудительно приравниваются к стоимости товаров + доставке: это отдельная сущность.
Из {number(payment_row.compared_orders)} заказов с платежами у {number(payment_row.difference_orders)}
разница превышает 0.01 BRL по модулю; суммарная знаковая разница
платежи − стоимость товаров − доставка = **R$ {number(payment_row.net_difference_brl, 2)}**.
Это диагностическая сверка, она не интерпретируется как прибыль или возвраты.
Хеши входов, SQL, конфигурация и версии — [manifest.json](manifest.json);
численные статистические результаты — [statistics.json](statistics.json).

Источник: [Olist, Brazilian E-Commerce Public Dataset](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce),
лицензия данных CC BY-NC-SA 4.0. Заказы анонимизированы; период источника 2016–2018.
Нет данных о визитах, CAC, марже, комиссиях, возвратах денег и рандомизации.
Поэтому здесь не оцениваются конверсия из трафика, прибыль, LTV бизнеса, NPS или фактический A/B-эффект.
История до начала выгрузки неизвестна, а полнота активности в конце августа не гарантирована.
"""
    (REPORTS / "executive_summary.md").write_text(report, encoding="utf-8")
    write_readme(summary_text, stats, total_gmv, total_orders)


def make_notebook():
    cells = []

    def md(source):
        cells.append({"cell_type": "markdown", "metadata": {}, "source": source.splitlines(keepends=True)})

    def code(source):
        cells.append({"cell_type": "code", "metadata": {}, "execution_count": None,
                      "outputs": [], "source": source.splitlines(keepends=True)})

    md("""# Olist: рост и качество покупательского опыта

Цель — выбрать направления улучшения опыта после покупки на открытых данных маркетплейса Olist.

Витрины и отчёт: `python scripts/build_portfolio.py`. Ниже исполняемые запросы к результату этой сборки.
Деньги — BRL, GMV без доставки. Доля доставленных — структура финальных статусов, не воронка.
Определения терминов — в [словаре проекта](../docs/glossary.md).
""")
    code('''from pathlib import Path
import json
import duckdb
import pandas as pd
from IPython.display import Image, display

candidates = [Path.cwd(), *Path.cwd().parents]
ROOT = next((p for p in candidates if (p / "sql/06_portfolio_marts.sql").exists()), None)
if ROOT is None:
    raise RuntimeError("Запустите notebook из корня проекта или notebooks/")
DB = ROOT / "data/portfolio.duckdb"
if not DB.exists():
    raise FileNotFoundError("Сначала выполните python scripts/build_portfolio.py")

def query(sql):
    with duckdb.connect(str(DB), read_only=True) as con:
        return con.execute(sql).df()

print("Конфигурация анализа:")
print(query("SELECT * FROM analysis_config").to_string(index=False))
''')
    md("""## Качество данных: одна строка на заказ

Позиции товаров и платежи отдельно суммируются по заказам до соединения таблиц (JOIN),
чтобы суммы не увеличились из-за размножения строк. Отзыв выбирается один на заказ.
Технический ключ покупателя — customer_unique_id. Проверки ниже должны завершиться без ошибок.
""")
    code('''checks = json.loads((ROOT / "reports/data_quality.json").read_text(encoding="utf-8"))
assert all(check["passed"] for check in checks)
display(pd.DataFrame(checks))
print(query("SELECT count(*) orders, count(DISTINCT order_id) unique_orders FROM p_orders").to_string(index=False))
''')
    md("""## 1. За счёт чего растёт GMV?

Сравниваем одинаковые полугодия. GMV = покупатели × заказы на покупателя × AOV.
Финальные два месяца показаны с оговоркой о близости к концу выгрузки.
""")
    code('''display(pd.read_csv(ROOT / "reports/tables/growth_decomposition.csv"))
display(query("SELECT month, delivered_orders, delivered_buyers, delivered_gmv, aov, gmv_mom_pct FROM a_monthly ORDER BY month"))
display(Image(filename=str(ROOT / "reports/figures/01_growth.png")))
''')
    md("""## 2. Возвращаются ли покупатели?

Повтор в первые 90 суток и возврат в календарном месяце отвечают разным вопросам.
Покупки в первые 24 часа проверяем отдельно. В знаменатель доли повторов входят покупатели
с полным 90-дневным окном; для месяцев M0–M6 неполные месяцы оставляем пустыми.
В июньской когорте полное окно есть только у покупателей начала месяца.
""")
    code('''display(query("SELECT * FROM a_repeat90 ORDER BY cohort_month"))
display(Image(filename=str(ROOT / "reports/figures/02_repeat90.png")))
display(Image(filename=str(ROOT / "reports/figures/03_retention.png")))
''')
    md("""## 3. Где сосредоточены опоздания?

Покупки 01.01.2017–30.06.2018, только доставленные заказы.
Запас времени до конца наблюдения уменьшает риск неполных данных о доставке, но не устраняет его полностью.
Приоритет определяем по абсолютному числу поздних заказов и доле с явным знаменателем.
""")
    code('''display(query("SELECT * FROM a_service_segments ORDER BY late_orders DESC LIMIT 10"))
display(Image(filename=str(ROOT / "reports/figures/04_delivery.png")))
''')
    md("""## 4. Как связаны доставка, отзывы и повтор?

Оценки 1–2 считаем среди заказов с выбранным отзывом и известной своевременностью.
95%-й доверительный интервал разницы рассчитан повторной выборкой по покупателям
с сохранением всех заказов выбранного покупателя — кластерным бутстрапом.
Сравнение при одинаковом составе по месяцу покупки и штату остаётся описательным.
Повтор после доставки имеет собственную точку отсчёта; нельзя использовать предшествовавшие ему заказы.
""")
    code('''statistics = json.loads((ROOT / "reports/statistics.json").read_text(encoding="utf-8"))
display(query("SELECT * FROM a_review_delivery ORDER BY is_late"))
print(json.dumps({k: statistics[k] for k in ["review_low_score", "review_after_delivery", "month_state_standardized_difference_pp"]}, ensure_ascii=False, indent=2))
display(pd.read_csv(ROOT / "reports/tables/repeat_after_delivery_by_lateness.csv"))
display(Image(filename=str(ROOT / "reports/figures/05_reviews.png")))
''')
    md("""## 5. Ассортимент и проверяемое решение

Категории — контекст спроса. Без маржи и экспозиции нельзя обосновать инвестиции только GMV.
Рекомендация: диагностика логистики и эксперимент проактивной коммуникации при риске задержки.
Случайное назначение варианта происходит по покупателю до уведомления; наблюдение длится до 150 дней.
В знаменателе остаются все назначенные участники, включая покупателей с недоставленным заказом.
Исходная доля ниже — приближение по покупателям с доставленным заказом и полным окном после получения.
Перед тестом её нужно уточнить на наблюдательной группе повышенного риска по правилам будущего теста,
без исключений по последующим событиям.
""")
    code('''display(query("SELECT category_name, gmv, gmv_share_pct FROM a_categories ORDER BY gmv DESC LIMIT 10"))
display(Image(filename=str(ROOT / "reports/figures/06_categories.png")))
print(json.dumps(statistics["experiment"], ensure_ascii=False, indent=2))
''')
    md("""## Выводы и границы применимости

Численные выводы и действия — в [готовом отчёте](../reports/executive_summary.md).
Дизайн будущего теста — в [experiment_design.md](../docs/experiment_design.md).

Это наблюдательные исторические данные. Статусы/отзывы — финальный снимок, история слева и справа ограничена.
Связь опоздания с низкой оценкой не доказывает влияние задержки. Нет данных о трафике, комиссиях и затратах:
не рассчитываем долю посетителей, совершивших покупку, стоимость привлечения, прибыль,
ценность клиента для бизнеса, NPS и фактический эффект A/B-теста.
Источник: [Olist / Kaggle](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce), CC BY-NC-SA4.0.
""")
    # Execute ordinary Python cells in order and embed their actual outputs.
    # No notebook runner is needed; any exception fails the build.
    namespace = {"__name__": "__main__"}
    count = 0
    for cell in cells:
        if cell["cell_type"] != "code":
            continue
        count += 1
        cell["execution_count"] = count
        outputs = cell["outputs"]

        def capture_display(value):
            if hasattr(value, "_repr_png_"):
                blob = value._repr_png_()
                if blob:
                    encoded = blob if isinstance(blob, str) else base64.b64encode(blob).decode("ascii")
                    outputs.append({"output_type": "display_data", "metadata": {},
                                    "data": {"image/png": encoded,
                                             "text/plain": ["<image>"]}})
                    return
            content = {"text/plain": [str(value)]}
            if hasattr(value, "_repr_html_"):
                content["text/html"] = [value._repr_html_()]
            outputs.append({"output_type": "display_data", "metadata": {}, "data": content})

        namespace["display"] = capture_display
        stream = io.StringIO()
        with redirect_stdout(stream):
            exec(compile("".join(cell["source"]), f"notebook-cell-{count}", "exec"), namespace)
        if stream.getvalue():
            outputs.append({"output_type": "stream", "name": "stdout", "text": stream.getvalue().splitlines(keepends=True)})
    notebook = {"cells": cells, "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                 "language_info": {"name": "python", "version": platform.python_version()}},
                "nbformat": 4, "nbformat_minor": 5}
    for i, cell in enumerate(cells):
        cell["id"] = f"olist-{i:02d}"
    (ROOT / "notebooks/02_product_analysis.ipynb").write_text(
        json.dumps(notebook, ensure_ascii=False, indent=1), encoding="utf-8")


def write_readme(summary, stats, total_gmv, total_orders):
    readme = f"""# Olist · продуктовая аналитика маркетплейса

**Вопрос:** как растёт маркетплейс и где улучшать опыт покупателя после заказа?

{summary}

![Качество доставки и отзывы](reports/figures/05_reviews.png)

## Посмотреть результат

- [Интерактивный отчёт Power BI](powerbi/Olist.pbix).
- [Редактируемый проект Power BI](powerbi/project/Olist.pbip).
- [Аналитический отчёт с выводами и действиями](reports/executive_summary.md).
- [Исполненный notebook с SQL-запросами и графиками](notebooks/02_product_analysis.ipynb).
- [Задача исследования и критерии готовности](docs/project_brief.md).
- [Словарь метрик, окна наблюдения и ограничения](docs/metrics.md).
- [Словарь терминов проекта](docs/glossary.md).
- [Дизайн будущего продуктового эксперимента](docs/experiment_design.md).
- [Руководство по Power BI](powerbi/README.md).
- [Результаты и порядок проверок](docs/validation.md).

Аналитический кейс на открытых исторических данных маркетплейса.
Анализ содержит {number(total_orders)} доставленных заказов бизнес-периода и R$ {number(total_gmv,2)} GMV товаров.
GMV не является выручкой маркетплейса; наблюдательные различия не доказывают причинный эффект.

## Воспроизвести

Python 3.13 — проверенная среда. Команды запускаются из корня проекта:

```powershell
python -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt
.\\.venv\\Scripts\\python.exe scripts/build_portfolio.py
.\\.venv\\Scripts\\python.exe scripts/export_powerbi_service.py
.\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v
.\\.venv\\Scripts\\python.exe scripts/validate_powerbi.py
```

В существующей среде достаточно последних четырёх команд. На macOS/Linux используйте `.venv/bin/python`.
Для интерактивного открытия notebook дополнительно нужен Jupyter; при сборке его код выполняется автоматически.
Сборщик сам записывает все таблицы, графики, численные выводы и исполненный notebook.
Он использует исходные CSV и отдельную `data/portfolio.duckdb`; для пересборки достаточно этих CSV и кода.

Источник — [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce),
анонимизированные заказы 2016–2018, лицензия данных **CC BY-NC-SA 4.0**.
Скачайте и распакуйте 9 CSV в `data/` с оригинальными именами:

```text
olist_customers_dataset.csv
olist_geolocation_dataset.csv
olist_orders_dataset.csv
olist_order_items_dataset.csv
olist_order_payments_dataset.csv
olist_order_reviews_dataset.csv
olist_products_dataset.csv
olist_sellers_dataset.csv
product_category_name_translation.csv
```

При публикации сохраняйте атрибуцию и условия лицензии данных.
Исходные CSV, локальные базы данных, кеши, резервные копии, учебные черновики
и локальное окружение исключены из текущей версии репозитория.

## Как устроен анализ

```text
CSV → scripts/build_portfolio.py → data/portfolio.duckdb
    → sql/06_portfolio_marts.sql    # заказы, позиции, покупатели
    → sql/07_product_metrics.sql    # рост, повтор, когорты, доставка, отзывы
    → проверки → reports/tables/ + reports/figures/
    → reports/executive_summary.md + notebooks/02_product_analysis.ipynb
```

Основной бизнес-период: январь 2017 — август 2018. Конец наблюдения покупок: 01.09.2018, не включительно.
Для сервиса используется январь 2017 — июнь 2018 с буфером на исполнение.
Повторные покупки считаются по `customer_unique_id`, только в зрелых окнах.
На заказ выбирается один отзыв по фиксированному правилу. Позиции и платежи суммируются до соединения таблиц.
Неопределённость разницы оценок проверяется повторной выборкой покупателей со всеми их заказами:
{stats['review_low_score']['iterations']} повторений, начальное значение генератора случайных чисел — 42.
Для долей повторных покупок рассчитаны интервалы Уилсона. Параметры и хеши входов — [manifest.json](reports/manifest.json).

Сборка прерывается при нарушении проверяемых правил качества; в текущей версии прошло {stats['data_quality_checks']} проверок.
Тесты проверяют финансовые сверки и синтетические случаи: размножение строк, последний отзыв,
доставку в обещанный день, NULL дат, границы 90 суток, зрелость когорт и временную последовательность.

## Чего данные не позволяют заключить

Нет визитов, показов, расходов на привлечение, комиссий, маржи и назначения A/B-варианта.
Не оцениваются доля посетителей, совершивших покупку, стоимость привлечения,
ценность клиента для бизнеса, прибыль, NPS или фактический эффект эксперимента.
Первый наблюдаемый покупатель может иметь неизвестную прошлую историю; статусы и отзывы взяты из итогового снимка.
Полнота конца периода не гарантирована. Предложения из отчёта — гипотезы для последующей проверки.
"""
    (ROOT / "README.md").write_text(readme, encoding="utf-8")


if __name__ == "__main__":
    main()
