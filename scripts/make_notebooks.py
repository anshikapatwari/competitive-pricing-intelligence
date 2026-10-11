"""Regenerate the four analysis notebooks. Cells are thin wrappers over the tested `pricing` modules,
so the logic lives in one place. Run: python scripts/make_notebooks.py [--execute-check]

--execute-check runs every code cell against SYNTHETIC data in a temp database (a smoke test that
the notebooks' code is valid; it does not execute or save notebook outputs).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]

SETUP = '''import sys; sys.path.insert(0, "..")
import numpy as np, pandas as pd, matplotlib.pyplot as plt
from pathlib import Path
from pricing.db import get_engine, load_snapshots, load_scrape_log
from pricing.clean import clean_snapshots, dim_products, gap_report
from pricing import analysis as an
from pricing.export import load_matches
raw = load_snapshots(get_engine())
clean, report = clean_snapshots(raw)
events = an.discount_events(clean)
print(f"{len(raw):,} raw rows -> {len(clean):,} clean rows, {clean.product_url.nunique()} products")'''

NOTEBOOKS = {
"01_data_quality_check": [
("md", "# 01 · Data-quality check\nRun weekly, not just at the end. Flags stale products (no snapshot in 48h — a broken selector looks like this), impossible values, duplicates, and scrape-log failures."),
("code", SETUP),
("code", '''pd.Series(report, name="rows")'''),
("code", '''gaps = gap_report(clean)
print("Stale (no snapshot in 48h):", len(gaps["stale_products"]))
pd.DataFrame(gaps["stale_products"]).head(20)'''),
("code", '''pd.DataFrame(gaps["historic_gaps"]).sort_values("gap_hours", ascending=False).head(20) if gaps["historic_gaps"] else "No gaps > 48h"'''),
("code", '''# share of snapshots where no struck-through MRP was found: a sudden jump = broken MRP selector
raw["mrp_assumed"] = raw["parse_notes"].fillna("").str.contains("mrp_assumed_equal_price")
raw.assign(day=raw.scraped_at.dt.date).groupby(["platform", "day"]).mrp_assumed.mean().unstack(0).plot(figsize=(9, 3), title="Share of rows with MRP assumed");'''),
("code", '''log = load_scrape_log(get_engine())
log.groupby(["platform", "status"]).size().unstack(fill_value=0) if len(log) else "scrape_log is empty"'''),
],
"02_discount_cadence": [
("md", "# 02 · Discount cadence\nFrequency, depth and rhythm of discounting, per product, weekday and brand."),
("code", SETUP),
("code", '''cad = an.product_cadence(events, clean)
cad.describe().T'''),
("code", '''daily = clean.assign(day=clean.scraped_at.dt.tz_convert(an.IST).dt.date).groupby(["platform", "day"]).discount_pct.mean().unstack(0)
daily.plot(figsize=(9, 3.5), title="Average discount by platform (%)");'''),
("code", '''pd.DataFrame(an.weekday_pattern(events)).set_index("weekday").plot.bar(figsize=(7, 3), legend=False, title="Deeper-discount events by weekday");'''),
("code", '''an.brand_typology(clean, events).sort_values("avg_discount", ascending=False)'''),
("code", '''matches = load_matches(Path("../data/products.csv")) if (Path("../data/products.csv")).exists() else pd.DataFrame(columns=["product_url","match_id"])
an.first_mover(events, matches)'''),
],
"03_elasticity_analysis": [
("md", "# 03 · Does discounting move demand?\nEvent study: review velocity 7 days before vs after each discount change. **Correlational, and review velocity is a proxy for sales** — see README limitations."),
("code", SETUP),
("code", '''es = an.build_event_study(clean, events)
es["brand_tier"] = es.brand.map(an.brand_tiers(clean)) if len(es) else None
deeper = es[es.discount_change > 0] if len(es) else es
print(len(es), "eligible events;", len(deeper), "deeper-discount events;", int((~deeper.overlap).sum()) if len(deeper) else 0, "with no overlapping change")'''),
("code", '''fit = an.elasticity(deeper); fit'''),
("code", '''an.discount_floor(deeper)'''),
("code", '''if len(deeper):
    ax = deeper.plot.scatter("discount_pct", "velocity_change", figsize=(8, 4), alpha=.6)
    if fit.get("available"):
        xs = np.array([fit["x_min"], fit["x_max"]]); ax.plot(xs, fit["intercept"] + fit["slope"] * xs, "k-")'''),
("code", '''pd.DataFrame(an.lift_by_segment(deeper, "price_band")), pd.DataFrame(an.lift_by_segment(deeper, "brand_tier"))'''),
("code", '''an.margin_erosion_flags(deeper)[["product_url", "brand", "event_date", "discount_pct", "velocity_before", "velocity_after"]]'''),
],
"04_cross_platform_positioning": [
("md", "# 04 · Cross-platform positioning\nMatched products come from the manual `match_id` column in `data/products.csv`. Search-rank is not captured by the scraper, so that test is not run."),
("code", SETUP),
("code", '''import os
pcsv = Path(os.environ.get("PRODUCTS_CSV", "../data/products.csv"))
matches = load_matches(pcsv)
mp = an.matched_products(clean, matches)
pairs = pd.DataFrame(mp["pairs"]); print(len(pairs), "matched pairs")
pairs.sort_values("avg_gap_pct", key=abs, ascending=False).head(15) if len(pairs) else "no matched pairs yet"'''),
("code", '''pd.DataFrame(an.unexplained_gaps(mp["pairs"]))'''),
("code", '''dim = dim_products(clean).dropna(subset=["avg_rating"])
ax = dim.plot.scatter("avg_price", "avg_rating", s=np.sqrt(dim.latest_reviews.fillna(1)) * 2, c=dim.platform.map({"flipkart": "#2a78d6", "amazon": "#eb6834"}), alpha=.6, figsize=(8, 4.5))
ax.set_title("Value map (bubble = review count)");'''),
("code", '''an.response_lag(clean, matches)'''),
],
}


def build():
    out = ROOT / "notebooks"
    out.mkdir(exist_ok=True)
    for name, cells in NOTEBOOKS.items():
        nb = nbf.v4.new_notebook()
        nb.cells = [nbf.v4.new_markdown_cell(src) if kind == "md" else nbf.v4.new_code_cell(src) for kind, src in cells]
        nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
        nbf.write(nb, out / f"{name}.ipynb")
    print("wrote", len(NOTEBOOKS), "notebooks")


def smoke_check():
    import matplotlib
    matplotlib.use("Agg")
    sys.path.insert(0, str(ROOT))
    from sqlalchemy import create_engine
    from pricing.db import init_db
    from pricing.demo import generate
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = Path(d) / "t.db"
        eng = create_engine(f"sqlite:///{db}")
        init_db(eng)
        snaps, products = generate(n_products=40, days=35, seed=1)
        snaps.drop(columns=["snapshot_id"]).to_sql("price_snapshots", eng, if_exists="append", index=False)
        pcsv = Path(d) / "products.csv"
        products.to_csv(pcsv, index=False)
        os.environ.update(DATABASE_URL=f"sqlite:///{db}", PRODUCTS_CSV=str(pcsv))
        os.chdir(ROOT / "notebooks")
        for name, cells in NOTEBOOKS.items():
            ns: dict = {}
            for kind, src in cells:
                if kind == "code":
                    exec(compile(src, f"{name}", "exec"), ns)
            print("OK", name)


if __name__ == "__main__":
    build()
    if "--execute-check" in sys.argv:
        smoke_check()
