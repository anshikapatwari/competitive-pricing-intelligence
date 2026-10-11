"""Cross-sectional pricing analysis of the public Amazon India sales dataset.

Input : dataset/amazon.csv  (Kaggle "Amazon Sales Dataset", one snapshot, no time dimension)
Output: site/data/amazon_analysis.json

Every number on the page is computed here; nothing is hand-entered. The snapshot date is
derived from the `qid` timestamp embedded in the product links.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

BANDS = [(0, 500, "Under ₹500"), (500, 1000, "₹500-999"), (1000, 10**9, "₹1,000 and above")]
BRAND_FIX = {"zebronics": "Zebronics", "boat": "boAt", "ptron": "pTron", "realme": "realme", "jbl": "JBL", "oneplus": "OnePlus"}
MIN_BRAND_N = 3
RNG = np.random.default_rng(7)


def load(path: str) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(path)
    rep = {"raw_rows": len(raw)}
    d = raw.copy()
    for c in ("discounted_price", "actual_price"):
        d[c] = pd.to_numeric(d[c].astype(str).str.replace(r"[₹,]", "", regex=True), errors="coerce")
    d["discount"] = pd.to_numeric(d["discount_percentage"].astype(str).str.rstrip("%"), errors="coerce")
    d["rating"] = pd.to_numeric(d["rating"], errors="coerce")
    d["rating_count"] = pd.to_numeric(d["rating_count"].astype(str).str.replace(",", ""), errors="coerce")
    rep["unparseable_rating"] = int(d["rating"].isna().sum())
    rep["missing_rating_count"] = int(d["rating_count"].isna().sum())
    d["calc_discount"] = (1 - d["discounted_price"] / d["actual_price"]) * 100
    rep["discount_mismatch_gt_1pp"] = int((abs(d["calc_discount"] - d["discount"]) > 1).sum())
    rep["price_gt_mrp"] = int((d["discounted_price"] > d["actual_price"]).sum())
    rep["duplicate_product_rows"] = int(d["product_id"].duplicated().sum())
    d = d.drop_duplicates("product_id").copy()
    rep["unique_products"] = len(d)
    d = d.dropna(subset=["discounted_price", "actual_price", "discount"])
    d["top"] = d["category"].str.split("|").str[0]
    d["sub"] = d["category"].str.split("|").str[1].fillna("Other")
    d["leaf"] = d["category"].str.split("|").str[-1]
    first = d["product_name"].str.split().str[0].str.strip(",.-")
    d["brand"] = first.map(lambda s: BRAND_FIX.get(s.lower(), s))
    qid = d["product_link"].str.extract(r"qid=(\d{9,})")[0].dropna().astype(int)
    rep["snapshot_date"] = (datetime.fromtimestamp(int(qid.median()), tz=timezone.utc).date().isoformat()
                            if len(qid) else None)
    return d, rep


def spearman_ci(x: pd.Series, y: pd.Series, n_boot: int = 2000) -> dict:
    m = x.notna() & y.notna()
    x, y = x[m].to_numpy(), y[m].to_numpy()
    if len(x) < 8:
        return {"n": int(len(x)), "rho": None, "lo": None, "hi": None}
    rx, ry = pd.Series(x).rank().to_numpy(), pd.Series(y).rank().to_numpy()
    rho = float(np.corrcoef(rx, ry)[0, 1])
    idx = RNG.integers(0, len(x), (n_boot, len(x)))
    boots = []
    for i in idx:
        a, b = pd.Series(x[i]).rank().to_numpy(), pd.Series(y[i]).rank().to_numpy()
        boots.append(np.corrcoef(a, b)[0, 1])
    lo, hi = np.nanpercentile(boots, [2.5, 97.5])
    return {"n": int(len(x)), "rho": round(rho, 3), "lo": round(float(lo), 3), "hi": round(float(hi), 3)}


def r(v, k=1):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), k)


def build(d: pd.DataFrame, rep: dict) -> dict:
    hp = d[d["sub"].str.contains("Headphones", case=False)].copy()
    other_elec = d[(d["top"] == "Electronics") & ~d.index.isin(hp.index)]

    # ---- sub-category table
    sub = (d.groupby(["top", "sub"]).agg(n=("discount", "size"), median_discount=("discount", "median"),
                                         median_price=("discounted_price", "median"),
                                         median_mrp=("actual_price", "median"),
                                         median_rating=("rating", "median"),
                                         share_ge_60=("discount", lambda s: (s >= 60).mean() * 100))
           .reset_index().query("n >= 15").sort_values("median_discount", ascending=False))
    categories = [dict(top=x.top, sub=x["sub"], n=int(x.n), median_discount=r(x.median_discount),
                       median_price=r(x.median_price, 0), median_mrp=r(x.median_mrp, 0),
                       median_rating=r(x.median_rating, 2), share_ge_60=r(x.share_ge_60)) for _, x in sub.iterrows()]

    # ---- headphones brand table
    hp["brand_group"] = hp["brand"].where(hp["brand"].map(hp["brand"].value_counts()) >= MIN_BRAND_N, "Other brands")
    br = (hp.groupby("brand_group").agg(n=("discount", "size"), median_price=("discounted_price", "median"),
                                        median_mrp=("actual_price", "median"), median_discount=("discount", "median"),
                                        median_rating=("rating", "median"), ratings=("rating_count", "sum"))
          .reset_index().sort_values("n", ascending=False))
    brands = [dict(brand=x.brand_group, n=int(x.n), median_price=r(x.median_price, 0), median_mrp=r(x.median_mrp, 0),
                   median_discount=r(x.median_discount), median_rating=r(x.median_rating, 2),
                   ratings=int(x.ratings)) for _, x in br.iterrows()]

    # ---- headphones price bands
    def band(p):
        for lo, hi, name in BANDS:
            if lo <= p < hi:
                return name
    hp["band"] = hp["discounted_price"].map(band)
    bands = []
    for _, _, name in BANDS:
        g = hp[hp["band"] == name]
        if len(g):
            bands.append(dict(band=name, n=int(len(g)), median_discount=r(g["discount"].median()),
                              median_rating=r(g["rating"].median(), 2), median_ratings=r(g["rating_count"].median(), 0)))

    # ---- value map points
    points = [dict(name=x.product_name[:70], brand=x.brand_group, price=r(x.discounted_price, 0), rating=r(x.rating, 1),
                   ratings=r(x.rating_count, 0), discount=r(x.discount, 0)) for _, x in hp.dropna(subset=["rating"]).iterrows()]

    # ---- discount distribution (10-pt bins)
    bins = list(range(0, 101, 10))
    def hist(s):
        c, _ = np.histogram(s, bins=bins)
        return [round(float(v) / max(len(s), 1) * 100, 1) for v in c]
    dist = dict(labels=[f"{a}-{b}%" for a, b in zip(bins[:-1], bins[1:])], headphones=hist(hp["discount"]),
                other_electronics=hist(other_elec["discount"]), n_headphones=int(len(hp)), n_other=int(len(other_elec)))

    # ---- association tests (spearman, bootstrap CI)
    d["log_rc"] = np.log1p(d["rating_count"])
    hp["log_rc"] = np.log1p(hp["rating_count"])
    assoc = {
        "all_discount_vs_ratings": spearman_ci(d["discount"], d["log_rc"]),
        "all_discount_vs_rating": spearman_ci(d["discount"], d["rating"]),
        "headphones_discount_vs_ratings": spearman_ci(hp["discount"], hp["log_rc"]),
        "headphones_price_vs_rating": spearman_ci(hp["discounted_price"], hp["rating"]),
    }
    # within-subcategory view removes mix effects (cheap cables discount more than TVs)
    within = []
    for (top, s), g in d.groupby(["top", "sub"]):
        if len(g) >= 40:
            c = spearman_ci(g["discount"], g["log_rc"], n_boot=500)
            within.append(dict(sub=s, n=c["n"], rho=c["rho"], lo=c["lo"], hi=c["hi"]))
    within.sort(key=lambda x: (x["rho"] is None, x["rho"]))

    # ---- findings (all from computed values)
    f = []
    top_discount = categories[0]
    low_discount = categories[-1]
    hp_row = next((c for c in categories if "Headphones" in c["sub"]), None)
    if hp_row:
        rank = [c["sub"] for c in categories].index(hp_row["sub"]) + 1
        f.append(f"Headphones, earbuds and accessories carry a median discount of {hp_row['median_discount']:.0f}%, "
                 f"ranking {rank} of {len(categories)} sub-categories with at least 15 listings "
                 f"(highest: {top_discount['sub']} at {top_discount['median_discount']:.0f}%, lowest: "
                 f"{low_discount['sub']} at {low_discount['median_discount']:.0f}%).")
    f.append(f"{(hp['discount'] >= 60).mean() * 100:.0f}% of headphone listings are discounted by 60% or more, against "
             f"{(other_elec['discount'] >= 60).mean() * 100:.0f}% elsewhere in Electronics. A deep discount here is the "
             f"norm and largely reflects a high listed MRP, so the discount percentage is a weak signal on its own.")
    big = [b for b in brands if b["brand"] != "Other brands" and b["n"] >= MIN_BRAND_N]
    if len(big) >= 2:
        cheap = min(big, key=lambda b: b["median_price"])
        dear = max(big, key=lambda b: b["median_price"])
        deep = max(big, key=lambda b: b["median_discount"])
        f.append(f"Among brands with at least {MIN_BRAND_N} listings, {cheap['brand']} has the lowest median selling price "
                 f"(₹{cheap['median_price']:,.0f}) and {dear['brand']} the highest (₹{dear['median_price']:,.0f}). "
                 f"{deep['brand']} discounts deepest at a median of {deep['median_discount']:.0f}%.")
    a = assoc["headphones_discount_vs_ratings"]
    if a["rho"] is not None:
        f.append(f"Across {a['n']} headphone listings the rank correlation between discount depth and number of ratings is "
                 f"{a['rho']:+.2f} (95% bootstrap CI {a['lo']:+.2f} to {a['hi']:+.2f}). "
                 + ("The interval includes zero, so there is no clear relationship at this sample size." if a["lo"] <= 0 <= a["hi"]
                    else "The interval excludes zero, but this is an association within one snapshot, not evidence that discounts cause demand."))
    a = assoc["all_discount_vs_ratings"]
    f.append(f"Across all {a['n']:,} products the same correlation is {a['rho']:+.2f} (CI {a['lo']:+.2f} to {a['hi']:+.2f}): "
             f"more heavily discounted listings tend to have {'fewer' if a['rho'] < 0 else 'more'} ratings, "
             f"which is consistent with discounts being used on less popular items rather than discounts driving popularity.")

    meta = dict(source="Kaggle: Amazon Sales Dataset (Amazon.in listings)", snapshot_date=rep["snapshot_date"],
                n_products=rep["unique_products"], n_headphones=int(len(hp)), n_categories=len(categories),
                median_discount_all=r(d["discount"].median()),
                generated_at=datetime.now(timezone.utc).isoformat(), synthetic=False)
    return dict(meta=meta, cleaning=rep, categories=categories, brands=brands, bands=bands, points=points,
                distribution=dist, association=assoc, within_category=within, findings=f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="dataset/amazon.csv")
    ap.add_argument("--out", default="site/data/amazon_analysis.json")
    a = ap.parse_args(argv)
    d, rep = load(a.csv)
    out = build(d, rep)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, allow_nan=False, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {a.out}")
    print(f"{len(out['findings'])} findings, {out['meta']['n_products']} products")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
