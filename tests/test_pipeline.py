import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from pricing import analysis as an
from pricing.clean import clean_snapshots, dim_products, gap_report
from pricing.db import init_db, load_snapshots
from pricing.demo import PLANTED, generate
from pricing.export import build_dashboard

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)
NO_MATCH = pd.DataFrame(columns=["product_url", "match_id"])


def snaps(url, prices, mrp=1000, reviews=None, platform="flipkart", brand="B", start=T0, step_h=24):
    reviews = reviews if reviews is not None else [100 + 10 * i for i in range(len(prices))]
    return [dict(platform=platform, product_url=url, product_name=f"n{i}", brand=brand, mrp=mrp,
                 selling_price=p, discount_pct=None, rating=4.0, review_count=r, in_stock=True,
                 scraped_at=start + timedelta(hours=step_h * i), parse_notes=None)
            for i, (p, r) in enumerate(zip(prices, reviews))]


def test_cleaning_rules_are_counted_and_applied():
    rows = snaps("u1", [800, 800, 1200, 0, None, 700], reviews=[100, 120, 130, 140, 150, 110])
    rows.append(dict(rows[0]))                                     # exact duplicate
    clean, rep = clean_snapshots(pd.DataFrame(rows))
    assert rep["dropped_selling_gt_mrp"] == 1 and rep["dropped_null_or_zero_price"] == 2
    assert rep["dropped_duplicates"] == 1 and rep["clean_rows"] == 3
    assert (clean["selling_price"] <= clean["mrp"]).all()
    assert clean["discount_pct"].tolist() == [20.0, 20.0, 30.0]
    # review_count decrease (150 -> 110) is carried forward, never negative
    assert clean["review_count"].is_monotonic_increasing


def test_events_only_on_change_and_baseline_flagged():
    clean, _ = clean_snapshots(pd.DataFrame(snaps("u", [900, 900, 900, 700, 700, 900])))
    ev = an.discount_events(clean)
    assert ev["is_baseline"].tolist() == [True, False, False]
    assert ev["discount_pct"].tolist() == [10.0, 30.0, 10.0]
    assert ev["discount_change"].tolist()[1:] == [20.0, -20.0]


def test_dim_products_keys_on_url_not_name():
    clean, _ = clean_snapshots(pd.DataFrame(snaps("u", [900, 900, 900])))   # names n0,n1,n2 differ
    assert len(dim_products(clean)) == 1


def test_gap_detection():
    clean, _ = clean_snapshots(pd.DataFrame(snaps("u1", [900] * 3) + snaps("u2", [900] * 8)))
    g = gap_report(clean)
    assert [s["product_url"] for s in g["stale_products"]] == ["u1"]
    gappy = snaps("u3", [900] * 3) + snaps("u3", [900] * 2, start=T0 + timedelta(days=6))
    clean2, _ = clean_snapshots(pd.DataFrame(gappy))
    assert len(gap_report(clean2)["historic_gaps"]) == 1


def test_event_study_known_answer():
    # 10 reviews/day for 10 days, discount starts day 10, then 30 reviews/day
    prices = [900] * 10 + [600] * 11
    reviews = [1000 + 10 * i for i in range(10)] + [1090 + 30 * (i + 1) for i in range(11)]
    clean, _ = clean_snapshots(pd.DataFrame(snaps("u", prices, reviews=reviews)))
    es = an.build_event_study(clean, an.discount_events(clean))
    assert len(es) == 1
    r = es.iloc[0]
    assert r["discount_pct"] == 40.0
    assert r["velocity_before"] == pytest.approx(10.0, abs=0.01)
    assert r["velocity_after"] == pytest.approx(30.0, abs=0.01)
    assert r["velocity_lift"] == pytest.approx(2.0, abs=0.01)


def test_event_study_skips_events_without_coverage_or_across_outages():
    clean, _ = clean_snapshots(pd.DataFrame(snaps("u", [900, 900, 600] + [600] * 12)))
    assert an.build_event_study(clean, an.discount_events(clean)).empty      # no 7d history before
    a = snaps("v", [900] * 10)
    b = snaps("v", [600] * 10, start=T0 + timedelta(days=16))                # 6-day outage
    clean2, _ = clean_snapshots(pd.DataFrame(a + b))
    assert an.build_event_study(clean2, an.discount_events(clean2)).empty


def test_elasticity_and_floor_unavailable_with_too_little_data():
    es = pd.DataFrame({"discount_pct": [10, 20, 30], "velocity_change": [1, 2, 3],
                       "velocity_lift": [.1, .2, .3]})
    assert an.elasticity(es)["available"] is False
    assert an.discount_floor(es)["available"] is False
    assert an.elasticity(pd.DataFrame())["available"] is False


def test_pipeline_recovers_planted_demand_response():
    raw, _ = generate(n_products=60, days=35, seed=42)
    clean, _ = clean_snapshots(raw)
    es = an.build_event_study(clean, an.discount_events(clean))
    deeper = es[es["discount_change"] > 0]
    fit = an.elasticity(deeper)
    assert fit["available"] and fit["slope"] > 0 and fit["p_value"] < 0.01
    assert fit["slope_ci95"][0] > 0
    floor = an.discount_floor(deeper)
    assert floor["available"] and abs(floor["floor_pct"] - PLANTED["floor"]) <= 5
    assert floor["mean_above"] > floor["mean_below"] and floor["p_value"] < 0.05


def test_null_world_finds_no_floor():
    """If demand is unrelated to discount depth, the permutation test must not call it significant."""
    rng = np.random.default_rng(0)
    es = pd.DataFrame({"discount_pct": rng.choice([5, 10, 20, 30, 40, 50], 120),
                       "velocity_lift": rng.normal(0.1, 0.3, 120)})
    f = an.discount_floor(es)
    assert (not f["available"]) or f["p_value"] > 0.05


def test_cross_platform_outputs():
    raw, products = generate(n_products=40, days=35, seed=3)
    clean, _ = clean_snapshots(raw)
    m = products[["product_url", "match_id"]]
    pairs = an.matched_products(clean, m)["pairs"]
    assert len(pairs) == 20
    assert all(p["avg_price_a"] > 0 and 0 <= p["share_days_a_cheaper"] <= 1 for p in pairs)
    lag = an.response_lag(clean, m)
    assert lag["n_responded"] <= lag["n_observable"] <= lag["n_drops"]
    assert lag["median_lag_days"] is None or lag["median_lag_days"] >= 0


def test_unexplained_gap_logic():
    pairs = [dict(match_id="a", avg_gap_pct=10, pricier_rating_edge=0.0),    # pricier, no rating edge
             dict(match_id="b", avg_gap_pct=10, pricier_rating_edge=0.5),    # justified by rating
             dict(match_id="c", avg_gap_pct=1, pricier_rating_edge=0.0),     # too small
             dict(match_id="d", avg_gap_pct=-6, pricier_rating_edge=None)]
    assert [p["match_id"] for p in an.unexplained_gaps(pairs)] == ["a", "d"]


def test_dashboard_json_is_strict_and_handles_empty():
    empty = build_dashboard(pd.DataFrame(), pd.DataFrame(), NO_MATCH)
    assert empty["meta"]["has_data"] is False
    raw, products = generate(n_products=20, days=20, seed=5)
    d = build_dashboard(raw, pd.DataFrame(), products[["product_url", "match_id"]], synthetic=True)
    json.dumps(d, allow_nan=False)
    assert d["meta"]["synthetic"] is True and d["meta"]["n_products"] == 20


def test_dashboard_with_only_day_one_does_not_crash():
    d = build_dashboard(pd.DataFrame(snaps("u", [900]) + snaps("w", [500], mrp=600)), pd.DataFrame(), NO_MATCH)
    assert d["meta"]["has_data"] and d["elasticity"]["available"] is False


def test_sql_views_match_pandas_events(tmp_path):
    raw, _ = generate(n_products=10, days=20, seed=9)
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(eng)
    raw.drop(columns=["snapshot_id"]).to_sql("price_snapshots", eng, if_exists="append", index=False)
    sql = (ROOT / "sql" / "02_kpi_views.sql").read_text(encoding="utf-8")
    with eng.begin() as c:
        body = "\n".join(ln for ln in sql.splitlines() if not ln.strip().startswith("--"))
        for stmt in [s for s in body.split(";") if s.strip()]:
            c.execute(text(stmt))
        n_sql = c.execute(text("SELECT COUNT(*) FROM fact_discount_events WHERE NOT is_baseline")).scalar()
        n_dim = c.execute(text("SELECT COUNT(*) FROM dim_products")).scalar()
    clean, _ = clean_snapshots(load_snapshots(eng))
    ev = an.discount_events(clean)
    assert n_sql == int((~ev["is_baseline"]).sum()) and n_dim == clean["product_url"].nunique()


def test_match_suggestions_same_brand_only():
    from pricing.matching import suggest
    p = pd.DataFrame([
        ("flipkart", "f1", "Acme Buds Pro Bluetooth Earbuds (Black)", "Acme"),
        ("amazon", "a1", "Acme Buds Pro True Wireless Earbuds with Mic", "Acme"),
        ("amazon", "a2", "Acme Buds Pro", "OtherBrand"),
        ("flipkart", "f2", "Zed Over-Ear Studio", "Zed"),
        ("amazon", "a3", "Completely Different Thing", "Zed"),
    ], columns=["platform", "product_url", "product_name", "brand"])
    s = suggest(p)
    assert s["url_a"].tolist() == ["a1"] and s["url_b"].tolist() == ["f1"]   # platforms sorted: amazon first
