"""Synthetic data generator — for tests and UI preview ONLY.

Everything produced here is invented. The brands are fictional, prices are random, and the
demand response is *planted* (a known discount floor and slope) so the test-suite can check
that the analysis recovers it. Output must never be presented as scraped market data; the
dashboard shows a prominent banner whenever `meta.synthetic` is true.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .export import build_dashboard

BRANDS = ["Aurora Audio", "Nimbus", "Kestrel", "Tandem Sound", "Orbit Labs", "Lumen"]
LINES = ["Pods Lite", "Pods Pro", "Bass Air", "Neo Buds", "Studio Buds", "Air ANC", "Beat Sync", "Duo Buds", "Flex Pods", "Core ANC"]
IST = timezone(timedelta(hours=5, minutes=30))

PLANTED = {"floor": 25.0, "lift_per_pp_above_floor": 0.05, "noise_sd": 0.4}


def generate(n_products: int = 60, days: int = 35, seed: int = 42,
             start: datetime | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (snapshots, products). Half the products have a counterpart on the other platform."""
    rng = np.random.default_rng(seed)
    start = start or datetime(2026, 9, 1, 9, 0, tzinfo=IST)
    n_pairs = n_products // 2
    specs = []
    for i in range(n_pairs):
        brand = BRANDS[i % len(BRANDS)]
        base_mrp = float(rng.choice([999, 1499, 1999, 2999, 4999, 7999, 12999]))
        pop = float(rng.uniform(2, 40))                        # baseline reviews per day
        rating = float(np.clip(rng.normal(4.1, 0.3), 3.2, 4.8))
        for plat in ("flipkart", "amazon"):
            specs.append(dict(match_id=f"M{i:03d}", platform=plat, brand=brand,
                              name=f"{brand} {LINES[i % len(LINES)]} {i // len(LINES) + 2}", base_mrp=base_mrp, pop=pop,
                              rating=rating + (rng.normal(0, 0.1)),
                              url=f"https://example.invalid/{plat}/{brand.split()[0].lower()}-{i:03d}"))
    rows, plan = [], {}
    # discount schedules: per product a list of (day_start, depth); amazon lags flipkart on some
    for s in specs:
        if s["platform"] == "flipkart":
            sched, d = [], int(rng.integers(2, 6))
            while d < days - 1:
                sched.append((d, float(rng.choice([5, 10, 15, 20, 25, 30, 35, 40, 50]))))
                d += int(rng.integers(5, 12))
            plan[s["match_id"]] = sched
    for s in specs:
        sched = plan[s["match_id"]]
        if s["platform"] == "amazon":
            follow = rng.random() < 0.6
            sched = [(d + int(rng.integers(1, 4)), x) for d, x in sched] if follow else \
                    [(d + int(rng.integers(0, 3)), float(rng.choice([5, 10, 20]))) for d, _ in sched[::2]]
        depth = np.zeros(days)
        for d, x in sched:
            end = min(days, d + int(rng.integers(4, 9)))
            depth[d:end] = x
        reviews = int(rng.integers(200, 8000))
        for day in range(days):
            # planted demand model
            lift = max(0.0, depth[day] - PLANTED["floor"]) * PLANTED["lift_per_pp_above_floor"]
            rate = s["pop"] * (1 + lift) * max(0.05, 1 + rng.normal(0, PLANTED["noise_sd"] * 0.5))
            reviews += int(rng.poisson(max(rate, 0.1)))
            mrp = s["base_mrp"]
            price = round(mrp * (1 - depth[day] / 100))
            if rng.random() < 0.03:                              # missed scrape (gap)
                continue
            rows.append(dict(platform=s["platform"], product_url=s["url"], product_name=s["name"],
                             brand=s["brand"], mrp=mrp, selling_price=price,
                             discount_pct=round((mrp - price) / mrp * 100, 2), rating=round(s["rating"], 1),
                             review_count=reviews, in_stock=True,
                             scraped_at=(start + timedelta(days=day, minutes=int(rng.integers(0, 40)))).astimezone(timezone.utc),
                             parse_notes=None))
    snaps = pd.DataFrame(rows)
    snaps.insert(0, "snapshot_id", range(1, len(snaps) + 1))
    products = pd.DataFrame([{"platform": s["platform"], "product_url": s["url"], "product_name": s["name"],
                              "brand": s["brand"], "match_id": s["match_id"]} for s in specs])
    return snaps, products


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Write a SYNTHETIC demo dashboard JSON for UI preview")
    ap.add_argument("--out", default="site/data/demo.json")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)
    snaps, products = generate(seed=args.seed)
    matches = products[["product_url", "match_id"]]
    dash = build_dashboard(snaps, pd.DataFrame(), matches, synthetic=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(dash, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote SYNTHETIC demo data to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
