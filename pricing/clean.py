"""Cleaning rules from the project guide, with a report of what each rule removed."""
from __future__ import annotations

import numpy as np
import pandas as pd

NUMERIC = ["mrp", "selling_price", "discount_pct", "rating", "review_count"]


def clean_snapshots(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Return (clean snapshots, report).

    Rules (applied in order, each row counted once, under the first rule it breaks):
      1. selling_price or mrp null / zero / negative  -> scrape error
      2. selling_price > mrp                          -> scrape error
      3. exact duplicate (product_url, scraped_at)    -> duplicate snapshot
    discount_pct is recomputed from mrp and selling_price rather than trusted.
    review_count is made monotonic per product (a decrease is treated as noise and the
    previous value is carried forward), so velocity is never negative.
    """
    df = raw.copy()
    for col in NUMERIC:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["scraped_at"] = pd.to_datetime(df["scraped_at"], utc=True)

    report = {"raw_rows": int(len(df))}

    bad_price = ~((df["selling_price"] > 0) & (df["mrp"] > 0))
    report["dropped_null_or_zero_price"] = int(bad_price.sum())
    df = df[~bad_price]

    over = df["selling_price"] > df["mrp"]
    report["dropped_selling_gt_mrp"] = int(over.sum())
    df = df[~over]

    dup = df.duplicated(subset=["product_url", "scraped_at"], keep="first")
    report["dropped_duplicates"] = int(dup.sum())
    df = df[~dup]

    df = df.sort_values(["product_url", "scraped_at"]).reset_index(drop=True)
    df["discount_pct"] = ((df["mrp"] - df["selling_price"]) / df["mrp"] * 100).round(2)

    g = df.groupby("product_url")["review_count"]
    filled = g.ffill()
    report["review_count_decreases_smoothed"] = int(
        (filled.groupby(df["product_url"]).diff() < 0).sum())
    df["review_count"] = filled.groupby(df["product_url"]).cummax()

    report["clean_rows"] = int(len(df))
    return df, report


def dim_products(clean: pd.DataFrame) -> pd.DataFrame:
    """One row per product_url. Name/brand come from the latest snapshot, never the name key."""
    last = clean.sort_values("scraped_at").groupby("product_url").tail(1)
    first = clean.groupby("product_url")["scraped_at"].min().rename("first_seen")
    out = last[["product_url", "platform", "product_name", "brand"]].merge(
        first, left_on="product_url", right_index=True)
    stats = clean.groupby("product_url").agg(
        avg_price=("selling_price", "mean"), avg_rating=("rating", "mean"),
        latest_reviews=("review_count", "max"), n_snapshots=("selling_price", "count"))
    out = out.merge(stats, left_on="product_url", right_index=True).reset_index(drop=True)
    out["product_id"] = out["product_url"].map(short_id)
    out["brand"] = out["brand"].fillna("Unknown")
    return out


def short_id(url: str) -> str:
    import hashlib
    return hashlib.sha1(url.encode()).hexdigest()[:8]


def gap_report(clean: pd.DataFrame, now: pd.Timestamp | None = None,
               max_gap_hours: float = 48) -> dict:
    """Stale products (no snapshot in 48h) and historic gaps > 48h, per the guide's QC step."""
    if clean.empty:
        return {"stale_products": [], "historic_gaps": [], "latest_snapshot": None}
    now = now or clean["scraped_at"].max()
    last = clean.groupby("product_url")["scraped_at"].max()
    stale = last[(now - last) > pd.Timedelta(hours=max_gap_hours)]
    gaps = []
    for url, grp in clean.groupby("product_url"):
        d = grp["scraped_at"].diff()
        for end, delta in d[d > pd.Timedelta(hours=max_gap_hours)].items():
            gaps.append({"product_url": url, "gap_hours": round(delta.total_seconds() / 3600, 1),
                         "ended_at": grp.loc[end, "scraped_at"].isoformat()})
    return {
        "stale_products": [{"product_url": u, "last_seen": t.isoformat()} for u, t in stale.items()],
        "historic_gaps": gaps,
        "latest_snapshot": now.isoformat(),
    }
