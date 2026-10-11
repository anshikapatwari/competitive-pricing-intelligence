"""The three analyses from the project guide.

Everything here takes cleaned snapshots (see clean.py) and returns plain DataFrames / dicts.
Thresholds are parameters, not hidden constants, so they can be defended and varied.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

IST = "Asia/Kolkata"


# --------------------------------------------------------------------------- events
def discount_events(clean: pd.DataFrame) -> pd.DataFrame:
    """fact_discount_events: one row when discount_pct changes vs the previous snapshot.

    The first snapshot of each product is kept as a baseline row (is_baseline=True) and is
    excluded from every statistic about *changes*. Event time is the snapshot that first
    showed the new value, so the true change happened somewhere in (prev_scraped_at, scraped_at].
    """
    df = clean.sort_values(["product_url", "scraped_at"]).copy()
    g = df.groupby("product_url")
    df["prev_discount"] = g["discount_pct"].shift()
    df["prev_scraped_at"] = g["scraped_at"].shift()
    keep = df["prev_discount"].isna() | (df["discount_pct"] != df["prev_discount"])
    ev = df[keep].copy()
    ev["is_baseline"] = ev["prev_discount"].isna()
    ev["discount_change"] = ev["discount_pct"] - ev["prev_discount"]
    ev["days_since_prev_event"] = (
        ev.groupby("product_url")["scraped_at"].diff().dt.total_seconds() / 86400)
    local = ev["scraped_at"].dt.tz_convert(IST)
    ev["event_date"] = local.dt.strftime("%Y-%m-%d")
    ev["weekday"] = local.dt.day_name()
    return ev.reset_index(drop=True)


# --------------------------------------------------------------------------- cadence
def product_cadence(events: pd.DataFrame, clean: pd.DataFrame) -> pd.DataFrame:
    changes = events[~events["is_baseline"]]
    agg = changes.groupby("product_url").agg(
        n_changes=("discount_pct", "size"),
        mean_days_between_changes=("days_since_prev_event", "mean"))
    depth = clean.groupby("product_url")["discount_pct"].agg(
        avg_discount="mean", max_discount="max", discount_std="std")
    out = depth.join(agg)
    out["n_changes"] = out["n_changes"].fillna(0).astype(int)
    return out.reset_index()


def weekday_pattern(events: pd.DataFrame) -> list[dict]:
    """Count of discount *increases* (a deeper discount appearing) by weekday of detection."""
    starts = events[(~events["is_baseline"]) & (events["discount_change"] > 0)]
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    counts = starts["weekday"].value_counts()
    return [{"weekday": d, "events": int(counts.get(d, 0))} for d in order]


def brand_typology(clean: pd.DataFrame, events: pd.DataFrame,
                   min_depth: float = 10.0, min_changes_per_product: float = 1.0,
                   volatile_std: float = 8.0) -> pd.DataFrame:
    """Steady / volatile / rarely-discounts, from depth, frequency and consistency.

    Cut-offs are judgement calls and are surfaced in the output so a reader can challenge them:
      rarely-discounts : mean depth < min_depth  OR  < min_changes_per_product changes/product
      volatile         : std of depth >= volatile_std percentage points
      steady           : everything else
    """
    brand = clean.groupby("brand")["discount_pct"].agg(
        avg_discount="mean", discount_std="std", max_discount="max")
    n_products = clean.groupby("brand")["product_url"].nunique().rename("n_products")
    n_changes = events[~events["is_baseline"]].groupby("brand").size().rename("n_changes")
    out = brand.join(n_products).join(n_changes)
    out["n_changes"] = out["n_changes"].fillna(0).astype(int)
    out["discount_std"] = out["discount_std"].fillna(0.0)
    out["changes_per_product"] = out["n_changes"] / out["n_products"]

    def label(r):
        if r.avg_discount < min_depth or r.changes_per_product < min_changes_per_product:
            return "rarely-discounts"
        return "volatile" if r.discount_std >= volatile_std else "steady"

    out["style"] = out.apply(label, axis=1)
    return out.reset_index()


# ----------------------------------------------------------------- review velocity
def review_velocity(clean: pd.DataFrame) -> pd.DataFrame:
    """Per consecutive snapshot pair: new reviews per day (the demand proxy)."""
    df = clean.sort_values(["product_url", "scraped_at"])
    g = df.groupby("product_url")
    out = df[["product_url", "scraped_at"]].copy()
    out["days_elapsed"] = g["scraped_at"].diff().dt.total_seconds() / 86400
    out["review_delta"] = g["review_count"].diff()
    out["velocity"] = out["review_delta"] / out["days_elapsed"]
    return out.dropna(subset=["velocity"])


# ----------------------------------------------------------------- event study
def assign_bands(values: pd.Series, labels: list[str]) -> pd.Series:
    """Split into len(labels) quantile bands by rank; works even with many ties."""
    r = values.rank(method="first", pct=True)
    n = len(labels)
    idx = np.minimum((np.ceil(r * n) - 1).astype(int), n - 1)
    return pd.Series([labels[i] for i in idx], index=values.index)


def build_event_study(clean: pd.DataFrame, events: pd.DataFrame, window_days: int = 7,
                      tolerance_days: float = 1.0, max_internal_gap_days: float = 3.0) -> pd.DataFrame:
    """Review velocity in the `window_days` before vs after each discount change.

    The change happened somewhere between the last snapshot that did not show it (prev) and the
    first that did (event), so that ambiguous interval is excluded from BOTH windows:
    before = [prev - window, prev], after = [event, event + window].
    Cumulative review count is linearly interpolated at those four times. An event is
    only eligible if snapshots cover both windows (within `tolerance_days`) with no internal
    gap above `max_internal_gap_days`, so a scraper outage is never read as "no reviews".
    `overlap` marks events with another discount change inside the +/- window (contaminated).
    """
    rows = []
    changes = events[~events["is_baseline"]]
    snaps_by_url = {u: g.sort_values("scraped_at") for u, g in clean.groupby("product_url")}
    for url, ev in changes.groupby("product_url"):
        snaps = snaps_by_url[url]
        if len(snaps) < 3:
            continue
        t0 = snaps["scraped_at"].iloc[0]
        t = (snaps["scraped_at"] - t0).dt.total_seconds().to_numpy() / 86400
        rc = snaps["review_count"].to_numpy(dtype=float)
        if np.isnan(rc).any():
            continue
        ev_times = (ev["scraped_at"] - t0).dt.total_seconds().to_numpy() / 86400
        prev_times = (ev["prev_scraped_at"] - t0).dt.total_seconds().to_numpy() / 86400
        avg_price = snaps["selling_price"].mean()
        for (_, e), te, tp in zip(ev.iterrows(), ev_times, prev_times):
            lo, hi = tp - window_days, te + window_days
            if t[0] > lo + tolerance_days or t[-1] < hi - tolerance_days:
                continue
            inside = t[(t >= lo) & (t <= hi)]
            pts = np.unique(np.concatenate([[max(lo, t[0])], inside, [min(hi, t[-1])]]))
            if np.diff(pts).max(initial=0) > max_internal_gap_days:
                continue
            c_lo, c_prev, c_ev, c_hi = np.interp([lo, tp, te, hi], t, rc)
            before, after = (c_prev - c_lo) / window_days, (c_hi - c_ev) / window_days
            others = ev_times[np.abs(ev_times - te) > 1e-9]
            rows.append({
                "product_url": url, "brand": e["brand"], "platform": e["platform"],
                "event_time": e["scraped_at"], "event_date": e["event_date"],
                "discount_pct": e["discount_pct"], "prev_discount": e["prev_discount"],
                "discount_change": e["discount_change"], "avg_price": avg_price,
                "velocity_before": before, "velocity_after": after,
                "velocity_change": after - before,
                "velocity_lift": (after / before - 1) if before > 0 else np.nan,
                "overlap": bool(((others >= lo) & (others <= hi)).any()),
            })
    es = pd.DataFrame(rows)
    if es.empty:
        return es
    es["price_band"] = assign_bands(es["avg_price"], ["budget", "mid", "premium"])
    return es


def elasticity(es: pd.DataFrame, x: str = "discount_pct", y: str = "velocity_change",
               n_boot: int = 1000, seed: int = 7) -> dict:
    """OLS slope of velocity change on discount depth, bootstrap 95% CI, Spearman rho."""
    if es.empty:
        return {"n": 0, "available": False, "reason": "no eligible discount events yet"}
    d = es[[x, y]].dropna()
    n = len(d)
    if n < 8 or d[x].nunique() < 3:
        return {"n": n, "available": False,
                "reason": f"only {n} usable discount events; need at least 8 with varied depth"}
    res = stats.linregress(d[x], d[y])
    rng = np.random.default_rng(seed)
    xs, ys = d[x].to_numpy(), d[y].to_numpy()
    slopes = []
    for _ in range(n_boot):
        i = rng.integers(0, n, n)
        if np.ptp(xs[i]) > 0:
            slopes.append(np.polyfit(xs[i], ys[i], 1)[0])
    rho, rho_p = stats.spearmanr(d[x], d[y])
    return {
        "n": n, "available": True, "slope": float(res.slope), "intercept": float(res.intercept),
        "r": float(res.rvalue), "p_value": float(res.pvalue),
        "slope_ci95": [float(np.percentile(slopes, 2.5)), float(np.percentile(slopes, 97.5))],
        "spearman_rho": float(rho), "spearman_p": float(rho_p),
        "x_min": float(xs.min()), "x_max": float(xs.max()),
    }


def discount_floor(es: pd.DataFrame, y: str = "velocity_lift", min_above: int = 5,
                   n_perm: int = 1000, seed: int = 11, lift_clip: tuple = (-1.0, 5.0)) -> dict:
    """Hinge model: response is flat below a threshold t and rises linearly above it,
    y = a + b * max(0, depth - t). t is chosen to minimise squared error.

    That is the guide's definition of a "discount floor": a depth below which demand barely
    moves, above which it climbs. y defaults to relative lift (comparable across popular and
    niche products; clipped to tame divide-by-small-baseline outliers). The p-value is a
    permutation test on the *best-of-all-thresholds* error reduction vs a flat model, so it is
    not flattered by the threshold search. With few events it is indicative only.
    """
    empty = {"available": False}
    if es.empty:
        return {**empty, "n": 0, "reason": "no eligible discount events yet"}
    d = es[["discount_pct", y]].replace([np.inf, -np.inf], np.nan).dropna()
    n = len(d)
    if n < 2 * min_above:
        return {**empty, "n": n, "reason": f"only {n} events; a floor needs at least {2 * min_above}"}
    x = d["discount_pct"].to_numpy(dtype=float)
    yv = np.clip(d[y].to_numpy(dtype=float), *lift_clip) if y == "velocity_lift" else d[y].to_numpy(dtype=float)
    cands = [t for t in np.unique(x) if (x > t).sum() >= min_above and (x <= t).sum() >= min_above]
    if not cands:
        return {**empty, "n": n, "reason": "no threshold leaves enough events on both sides"}
    hinges = [np.maximum(0.0, x - t) for t in cands]

    def best(v):
        vc = v - v.mean()
        sst = float(vc @ vc)
        top = (-1, -np.inf, 0.0)
        for k, h in enumerate(hinges):
            hc = h - h.mean()
            sh = float(hc @ hc)
            if sh <= 0:
                continue
            slope = float(hc @ vc) / sh
            gain = slope * float(hc @ vc)          # SSE_flat - SSE_hinge
            if slope > 0 and gain > top[1]:        # only "more discount -> more demand" shapes
                top = (k, gain, slope)
        return top + (sst,)

    k, gain, slope, sst = best(yv)
    if k < 0:
        return {**empty, "n": n, "reason": "no upward-sloping response found at any threshold"}
    rng = np.random.default_rng(seed)
    perm = [best(rng.permutation(yv))[1] for _ in range(n_perm)]
    p = (1 + sum(g >= gain for g in perm)) / (1 + n_perm)
    t = float(cands[k])
    above = x > t
    return {"available": True, "n": n, "metric": y, "floor_pct": t, "slope_per_pp_above": slope,
            "r2_gain": gain / sst if sst > 0 else None,
            "mean_below": float(yv[~above].mean()), "mean_above": float(yv[above].mean()),
            "n_below": int((~above).sum()), "n_above": int(above.sum()), "p_value": float(p)}


def lift_by_segment(es: pd.DataFrame, col: str) -> list[dict]:
    out = []
    if es.empty:
        return out
    for key, g in es.groupby(col):
        v = g["velocity_lift"].replace([np.inf, -np.inf], np.nan).dropna()
        out.append({"segment": str(key), "n": int(len(g)),
                    "median_lift": float(v.median()) if len(v) else None,
                    "mean_velocity_change": float(g["velocity_change"].mean()),
                    "avg_discount": float(g["discount_pct"].mean())})
    return out


def brand_tiers(clean: pd.DataFrame) -> pd.Series:
    """Brand tier by total review volume (terciles): niche / challenger / major."""
    per_product = clean.groupby(["brand", "product_url"])["review_count"].max()
    total = per_product.groupby(level=0).sum()
    return assign_bands(total, ["niche", "challenger", "major"])


def margin_erosion_flags(es: pd.DataFrame, min_depth: float = 30.0, max_lift: float = 0.10) -> pd.DataFrame:
    """Deeper-discount events landing at >= min_depth % after which velocity rose by <= max_lift."""
    if es.empty:
        return es
    f = es[(es["discount_pct"] >= min_depth) & (es["discount_change"] > 0)]
    f = f[f["velocity_after"] <= f["velocity_before"] * (1 + max_lift)]
    return f.sort_values("discount_pct", ascending=False)


# ----------------------------------------------------------- cross-platform
def matched_products(clean: pd.DataFrame, matches: pd.DataFrame) -> dict:
    """Compare products that share a manual `match_id` across platforms.

    Prices are compared on dates both platforms were scraped (last snapshot of each IST day).
    """
    if matches.empty:
        return {"pairs": [], "daily_gap": []}
    c = clean.merge(matches[["product_url", "match_id"]], on="product_url")
    if c.empty:
        return {"pairs": [], "daily_gap": []}
    c["date"] = c["scraped_at"].dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
    daily = c.sort_values("scraped_at").groupby(["match_id", "platform", "date"]).tail(1)
    pairs, daily_gap = [], []
    for mid, g in daily.groupby("match_id"):
        plats = sorted(g["platform"].unique())
        if len(plats) < 2:
            continue
        a, b = plats[0], plats[1]
        wide = g.pivot(index="date", columns="platform", values="selling_price").dropna(subset=[a, b])
        if wide.empty:
            continue
        rating = g.groupby("platform")["rating"].mean()
        gap = wide[a] - wide[b]
        pricier = a if gap.mean() > 0 else b
        other = b if pricier == a else a
        edge = rating.get(pricier, np.nan) - rating.get(other, np.nan)
        pairs.append({
            "match_id": str(mid), "brand": g["brand"].mode().iloc[0],
            "product_name": g.sort_values("scraped_at")["product_name"].iloc[-1],
            "platform_a": a, "platform_b": b, "days_compared": int(len(wide)),
            "avg_price_a": float(wide[a].mean()), "avg_price_b": float(wide[b].mean()),
            "avg_gap": float(gap.mean()),
            "avg_gap_pct": float(gap.mean() / min(wide[a].mean(), wide[b].mean()) * 100),
            "share_days_a_cheaper": float((gap < 0).mean()),
            "rating_a": _f(rating.get(a)), "rating_b": _f(rating.get(b)),
            "pricier_platform": pricier, "pricier_rating_edge": _f(edge),
        })
        for d, v in gap.items():
            daily_gap.append({"match_id": str(mid), "date": d, "gap": float(v)})
    return {"pairs": pairs, "daily_gap": daily_gap}


def _f(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)


def unexplained_gaps(pairs: list[dict], rating_margin: float = 0.2, min_gap_pct: float = 3.0) -> list[dict]:
    """Pairs where one platform is >= min_gap_pct pricier and not rated >= rating_margin higher."""
    out = []
    for p in pairs:
        edge = p["pricier_rating_edge"]
        justified = edge is not None and edge >= rating_margin
        if not justified and abs(p["avg_gap_pct"]) >= min_gap_pct:
            out.append(p)
    return sorted(out, key=lambda p: abs(p["avg_gap_pct"]), reverse=True)


def response_lag(clean: pd.DataFrame, matches: pd.DataFrame, min_drop_pct: float = 3.0,
                 horizon_days: float = 14.0) -> dict:
    """After a selling-price drop on one platform, days until the matched product drops on the other.

    A drop with no response is only counted as 'no response' if the data extends a full
    horizon past it; otherwise it is right-censored and left out of the response rate.
    """
    empty = {"n_drops": 0, "n_observable": 0, "n_responded": 0, "median_lag_days": None,
             "lags": [], "horizon_days": horizon_days, "min_drop_pct": min_drop_pct}
    if matches.empty:
        return empty
    c = clean.merge(matches[["product_url", "match_id"]], on="product_url").sort_values("scraped_at")
    if c.empty:
        return empty
    data_end = c["scraped_at"].max()
    c["prev_price"] = c.groupby("product_url")["selling_price"].shift()
    c["is_drop"] = c["prev_price"].notna() & (c["selling_price"] <= c["prev_price"] * (1 - min_drop_pct / 100))
    drops = c[c["is_drop"]]
    lags, n_observable, n_responded = [], 0, 0
    for _, d in drops.iterrows():
        other = c[(c["match_id"] == d["match_id"]) & (c["platform"] != d["platform"]) & c["is_drop"]]
        resp = other[(other["scraped_at"] >= d["scraped_at"] - pd.Timedelta(days=1)) &
                     (other["scraped_at"] <= d["scraped_at"] + pd.Timedelta(days=horizon_days))]
        fully_observed = data_end >= d["scraped_at"] + pd.Timedelta(days=horizon_days)
        if len(resp):
            n_observable += 1
            n_responded += 1
            lags.append(round(max((resp["scraped_at"].iloc[0] - d["scraped_at"]).total_seconds() / 86400, 0.0), 2))
        elif fully_observed and not c[(c["match_id"] == d["match_id"]) & (c["platform"] != d["platform"])].empty:
            n_observable += 1
    return {"n_drops": int(len(drops)), "n_observable": n_observable, "n_responded": n_responded,
            "median_lag_days": float(np.median(lags)) if lags else None, "lags": lags,
            "horizon_days": horizon_days, "min_drop_pct": min_drop_pct}


def first_mover(events: pd.DataFrame, matches: pd.DataFrame) -> dict:
    """For matched products: which platform shows a deeper-discount event first."""
    if matches.empty:
        return {"pairs": 0, "first_discount_wins": {}}
    e = events[(~events["is_baseline"]) & (events["discount_change"] > 0)].merge(
        matches[["product_url", "match_id"]], on="product_url")
    first = e.groupby(["match_id", "platform"])["scraped_at"].min().unstack()
    if first.shape[1] < 2:
        return {"pairs": 0, "first_discount_wins": {}}
    both = first.dropna()
    wins = {p: int((both[p] == both.min(axis=1)).sum()) for p in both.columns}
    return {"pairs": int(len(both)), "first_discount_wins": wins}
