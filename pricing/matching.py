"""Suggest cross-platform product matches for MANUAL review.

Matching is deliberately not automatic in the analysis: only rows with a `match_id` that you
confirmed in data/products.csv are compared. This tool just proposes candidates, using
brand equality + difflib string similarity on normalised names. Known limits: it cannot see
bundles, colour/regional SKUs or generation suffixes ("Pro" vs "Pro 2" score very high).
A production version should match on a shared identifier (EAN/UPC/model number).

    python -m pricing.matching data/products.csv
"""
from __future__ import annotations

import re
import sys
from difflib import SequenceMatcher

import pandas as pd

NOISE = re.compile(r"\b(with|bluetooth|wireless|earbuds|earphones|headphones|neckband|true|tws|in[- ]ear|"
                   r"over[- ]ear|black|blue|white|grey|gray|red|green|mic|microphone|hrs|playback)\b|[^a-z0-9 ]")


def normalise(name: str, brand: str = "") -> str:
    s = str(name).lower()
    if brand:
        s = s.replace(str(brand).lower(), " ")
    s = NOISE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def suggest(products: pd.DataFrame, min_score: float = 0.75) -> pd.DataFrame:
    """Best other-platform candidate per product, same brand only, above min_score."""
    p = products.copy()
    p["norm"] = [normalise(n, b) for n, b in zip(p["product_name"], p["brand"])]
    p["brand_key"] = p["brand"].astype(str).str.lower().str.strip()
    out = []
    plats = sorted(p["platform"].unique())
    if len(plats) < 2:
        return pd.DataFrame(columns=["platform_a", "url_a", "name_a", "platform_b", "url_b", "name_b", "score"])
    a_plat, b_plat = plats[0], plats[1]
    for a in p[p["platform"] == a_plat].itertuples():
        cands = p[(p["platform"] == b_plat) & (p["brand_key"] == a.brand_key)]
        best, best_s = None, 0.0
        for b in cands.itertuples():
            s = SequenceMatcher(None, a.norm, b.norm).ratio()
            if s > best_s:
                best, best_s = b, s
        if best is not None and best_s >= min_score:
            out.append({"platform_a": a_plat, "url_a": a.product_url, "name_a": a.product_name,
                        "platform_b": b_plat, "url_b": best.product_url, "name_b": best.product_name,
                        "score": round(best_s, 3)})
    return pd.DataFrame(out).sort_values("score", ascending=False).reset_index(drop=True) if out else pd.DataFrame(
        columns=["platform_a", "url_a", "name_a", "platform_b", "url_b", "name_b", "score"])


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/products.csv"
    sug = suggest(pd.read_csv(path, dtype=str))
    print(sug.to_string(index=False) if len(sug) else "No candidates. Add products for two platforms first.")
