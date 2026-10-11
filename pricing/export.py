"""Run the full analysis and write one JSON document that the static dashboard reads."""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import analysis as an
from .clean import clean_snapshots, dim_products, gap_report, short_id
from .db import get_engine, load_scrape_log, load_snapshots

COLLECTION_TARGET_DAYS = 28


def _plain(o):
    """Recursively convert numpy / pandas / NaN into strict-JSON-safe Python values."""
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    if isinstance(o, (np.bool_, bool)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if (math.isnan(o) or math.isinf(o)) else float(o)
    if o is pd.NaT:
        return None
    return o


def load_matches(products_csv: Path | None) -> pd.DataFrame:
    if products_csv is None or not Path(products_csv).exists():
        return pd.DataFrame(columns=["product_url", "match_id"])
    p = pd.read_csv(products_csv, dtype=str)
    if "match_id" not in p.columns:
        return pd.DataFrame(columns=["product_url", "match_id"])
    m = p.dropna(subset=["match_id"])
    m = m[m["match_id"].str.strip() != ""]
    return m[["product_url", "match_id"]].reset_index(drop=True)


def empty_dashboard(synthetic: bool, note: str) -> dict:
    return {"meta": {"generated_at": datetime.now(timezone.utc).isoformat(), "synthetic": synthetic,
                     "has_data": False, "note": note, "target_days": COLLECTION_TARGET_DAYS}}


def build_dashboard(raw: pd.DataFrame, log: pd.DataFrame, matches: pd.DataFrame,
                    synthetic: bool = False) -> dict:
    if raw.empty:
        return empty_dashboard(synthetic, "No snapshots in the database yet.")
    clean, report = clean_snapshots(raw)
    if clean.empty:
        return empty_dashboard(synthetic, "All snapshots failed validation; see data quality notes.")

    dim = dim_products(clean)
    events = an.discount_events(clean)
    cadence = an.product_cadence(events, clean)
    brand_style = an.brand_typology(clean, events)
    es = an.build_event_study(clean, events)
    tiers = an.brand_tiers(clean)
    if not es.empty:
        es["brand_tier"] = es["brand"].map(tiers)
    pid = dict(zip(dim["product_url"], dim["product_id"]))

    start, end = clean["scraped_at"].min(), clean["scraped_at"].max()
    window_days = (end - start).total_seconds() / 86400

    # ---- quality
    gaps = gap_report(clean)
    assumed = raw["parse_notes"].fillna("").str.contains("mrp_assumed_equal_price").mean() if "parse_notes" in raw else 0
    log_summary = []
    if not log.empty:
        for (plat, status), n in log.groupby(["platform", "status"]).size().items():
            log_summary.append({"platform": plat, "status": status, "n": int(n)})
    quality = {
        "cleaning": report,
        "stale_products": [{"product_id": pid.get(s["product_url"]), "last_seen": s["last_seen"]}
                           for s in gaps["stale_products"]],
        "historic_gaps": [{"product_id": pid.get(g["product_url"]), **{k: v for k, v in g.items() if k != "product_url"}}
                          for g in gaps["historic_gaps"]],
        "share_mrp_assumed_equal_price": float(assumed),
        "scrape_log_summary": log_summary,
    }

    # ---- daily series (last snapshot of each IST day)
    c = clean.copy()
    c["date"] = c["scraped_at"].dt.tz_convert(an.IST).dt.strftime("%Y-%m-%d")
    daily = c.sort_values("scraped_at").groupby(["product_url", "date"]).tail(1)
    dates = sorted(daily["date"].unique())
    didx = {d: i for i, d in enumerate(dates)}
    series = {}
    for url, g in daily.groupby("product_url"):
        price, mrp, disc = [None] * len(dates), [None] * len(dates), [None] * len(dates)
        for _, r in g.iterrows():
            i = didx[r["date"]]
            price[i], mrp[i], disc[i] = r["selling_price"], r["mrp"], r["discount_pct"]
        series[pid[url]] = {"price": price, "mrp": mrp, "discount": disc}
    plat_daily = (daily.groupby(["platform", "date"])
                  .agg(avg_discount=("discount_pct", "mean"), avg_price=("selling_price", "mean"),
                       n=("product_url", "nunique")).reset_index())

    # ---- products table
    prod = dim.merge(cadence, on="product_url", how="left")
    products = [{
        "id": r.product_id, "name": r.product_name, "brand": r.brand, "platform": r.platform,
        "url": r.product_url, "avg_price": r.avg_price, "avg_rating": r.avg_rating,
        "reviews": r.latest_reviews, "n_snapshots": r.n_snapshots,
        "n_changes": r.n_changes, "avg_discount": r.avg_discount, "max_discount": r.max_discount,
    } for r in prod.itertuples()]

    # ---- discount events (changes only)
    chg = events[~events["is_baseline"]].sort_values("scraped_at", ascending=False)
    event_rows = [{
        "id": pid[r.product_url], "name": r.product_name, "brand": r.brand, "platform": r.platform,
        "date": r.event_date, "from": r.prev_discount, "to": r.discount_pct, "change": r.discount_change,
        "price": r.selling_price,
    } for r in chg.head(1000).itertuples()]

    # ---- elasticity
    def pts(df):
        return [{"x": r.discount_pct, "y": r.velocity_change, "lift": r.velocity_lift,
                 "id": pid[r.product_url], "brand": r.brand, "band": r.price_band,
                 "tier": getattr(r, "brand_tier", None), "overlap": r.overlap, "date": r.event_date,
                 "up": r.discount_change > 0}
                for r in df.itertuples()]

    if es.empty:
        elasticity = {"n_events": 0, "available": False,
                      "reason": "No discount event has 7 days of review history on both sides yet."}
    else:
        deeper = es[es["discount_change"] > 0]          # a discount started or deepened
        clean_win = deeper[~deeper["overlap"]]
        erosion = an.margin_erosion_flags(deeper)
        elasticity = {
            "available": True, "n_events": int(len(es)), "n_deeper": int(len(deeper)),
            "n_clean_window": int(len(clean_win)),
            "fit_deeper": an.elasticity(deeper),
            "fit_lift": an.elasticity(deeper.assign(
                lift_clipped=deeper["velocity_lift"].clip(-1, 5)), y="lift_clipped"),
            "fit_clean_window": an.elasticity(clean_win),
            "floor": an.discount_floor(deeper),
            "by_price_band": an.lift_by_segment(deeper, "price_band"),
            "by_brand_tier": an.lift_by_segment(deeper, "brand_tier"),
            "points": pts(deeper),
            "erosion": [{"id": pid[r.product_url], "brand": r.brand, "date": r.event_date,
                         "discount_pct": r.discount_pct, "before": r.velocity_before,
                         "after": r.velocity_after, "avg_price": r.avg_price}
                        for r in erosion.itertuples()],
            "n_erosion": int(len(erosion)),
        }

    # ---- positioning
    value_map = [{"id": r.product_id, "name": r.product_name, "brand": r.brand, "platform": r.platform,
                  "price": r.avg_price, "rating": r.avg_rating, "reviews": r.latest_reviews}
                 for r in dim.itertuples() if pd.notna(r.avg_rating)]
    mp = an.matched_products(clean, matches)
    positioning = {
        "value_map": value_map,
        "matched_pairs": mp["pairs"],
        "unexplained_gaps": an.unexplained_gaps(mp["pairs"]),
        "daily_gap": mp["daily_gap"],          # per match_id/date; the page averages after filtering
        "response_lag": an.response_lag(clean, matches),
        "first_mover": an.first_mover(events, matches),
        "search_rank": {"available": False,
                        "reason": "Search-rank / bestseller position is not captured by the scraper "
                                  "(it only visits curated product pages), so the price-rank test is not run."},
    }

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "synthetic": synthetic, "has_data": True,
        "window_start": start.isoformat(), "window_end": end.isoformat(),
        "window_days": round(window_days, 1), "target_days": COLLECTION_TARGET_DAYS,
        "n_products": int(dim.shape[0]), "n_snapshots": int(len(clean)),
        "platforms": sorted(clean["platform"].unique().tolist()),
        "n_matched_products": len(mp["pairs"]),
        "n_discount_changes": int((~events["is_baseline"]).sum()),
        "avg_discount": float(clean["discount_pct"].mean()),
    }
    out = {
        "meta": meta, "quality": quality, "dates": dates, "series": series,
        "platform_daily": plat_daily.to_dict("records"), "products": products,
        "events": event_rows,
        "cadence": {"weekday": an.weekday_pattern(events), "brand_typology": brand_style.to_dict("records")},
        "elasticity": elasticity, "positioning": positioning,
    }
    return _plain(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Analyse the DB and write site/data/dashboard.json")
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--products", default="data/products.csv")
    ap.add_argument("--out", default="site/data/dashboard.json")
    ap.add_argument("--csv-snapshot", default=None, help="also dump raw snapshots to this CSV")
    args = ap.parse_args(argv)

    engine = get_engine(args.database_url)
    db_file = engine.url.database if engine.url.get_backend_name() == "sqlite" else None
    try:
        if db_file and not (Path(db_file).exists() and Path(db_file).stat().st_size):
            raise FileNotFoundError(db_file)          # don't create an empty DB as a side effect
        raw, log = load_snapshots(engine), load_scrape_log(engine)
    except Exception as exc:  # tables do not exist yet
        print(f"No data available ({exc.__class__.__name__}); writing empty dashboard.")
        raw, log = pd.DataFrame(), pd.DataFrame()
    dash = build_dashboard(raw, log, load_matches(Path(args.products)))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dash, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    if args.csv_snapshot and not raw.empty:
        Path(args.csv_snapshot).parent.mkdir(parents=True, exist_ok=True)
        raw.to_csv(args.csv_snapshot, index=False)
    print(f"Wrote {out} (has_data={dash['meta'].get('has_data')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
