"""Daily scrape runner.

    python -m scraper.run_scrape                 # all enabled platforms in products.csv
    python -m scraper.run_scrape --platform flipkart --limit 5
    python -m scraper.run_scrape --dry-run       # validate config + CSV, fetch nothing

Safety gates (all must pass before a single request is sent):
  * scheduler_config.yml -> tos_reviewed: true   (you read each site's Terms of Use)
  * SCRAPER_CONTACT env var set                  (goes in the User-Agent, so site owners can reach you)
Per product: robots.txt checked, 3-6 s randomised delay, no retries. A product failure is
logged and skipped; three consecutive block signals stop that platform for the run.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
from sqlalchemy import insert

from pricing.db import get_engine, init_db, price_snapshots, scrape_log

from .amazon_scraper import parse_amazon
from .common import Blocked, ParseError, PoliteFetcher, RobotsDisallowed
from .flipkart_scraper import parse_flipkart

PARSERS = {"flipkart": parse_flipkart, "amazon": parse_amazon}
MAX_CONSECUTIVE_BLOCKS = 3
CONFIG_PATH = Path(__file__).with_name("scheduler_config.yml")


def load_config(path: Path = CONFIG_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def check_gates(cfg: dict, env=os.environ) -> str:
    """Return the User-Agent, or raise SystemExit with the reason a run is not allowed."""
    if not cfg.get("tos_reviewed"):
        raise SystemExit("Refusing to scrape: set `tos_reviewed: true` in scraper/scheduler_config.yml "
                         "after reading each site's Terms of Use and robots.txt.")
    contact = env.get("SCRAPER_CONTACT", "").strip()
    if not contact:
        raise SystemExit("Refusing to scrape: set the SCRAPER_CONTACT env var (an email or repo URL "
                         "that identifies you in the User-Agent).")
    return f"student-pricing-project/1.0 (+contact: {contact})"


def run(products: pd.DataFrame, cfg: dict, fetcher, engine, run_id: str | None = None,
        platforms: list[str] | None = None, limit: int | None = None) -> dict:
    run_id = run_id or uuid.uuid4().hex[:12]
    enabled = platforms or [p for p, v in cfg.get("platforms", {}).items() if v.get("enabled")]
    summary = {"run_id": run_id, "ok": 0, "failed": 0, "skipped": 0, "halted": []}
    logs, rows = [], []
    consecutive_blocks = {p: 0 for p in enabled}

    def log(platform, url, status, detail="", http=None):
        logs.append({"run_id": run_id, "platform": platform, "product_url": url, "status": status,
                     "detail": detail[:500], "http_status": http, "logged_at": datetime.now(timezone.utc)})

    todo = products[products["platform"].isin(enabled)]
    if limit:
        todo = todo.groupby("platform").head(limit)
    for rec in todo.itertuples():
        plat = rec.platform
        if plat in summary["halted"]:
            summary["skipped"] += 1
            continue
        try:
            resp = fetcher.get(rec.product_url)
            row = PARSERS[plat](resp.text, rec.product_url, cfg.get("platforms", {}).get(plat, {}).get("selectors"))
            # curated names win: scraped titles vary between visits
            for col in ("product_name", "brand"):
                curated = getattr(rec, col, None)
                if isinstance(curated, str) and curated.strip():
                    row[col] = curated.strip()
            rows.append(row)
            consecutive_blocks[plat] = 0
            summary["ok"] += 1
            log(plat, rec.product_url, "ok", row.get("parse_notes") or "")
        except RobotsDisallowed:
            summary["skipped"] += 1
            log(plat, rec.product_url, "robots_disallowed")
        except Blocked as exc:
            summary["failed"] += 1
            consecutive_blocks[plat] += 1
            log(plat, rec.product_url, "blocked", str(exc))
            if consecutive_blocks[plat] >= MAX_CONSECUTIVE_BLOCKS:
                summary["halted"].append(plat)
                log(plat, "", "platform_halted", f"{MAX_CONSECUTIVE_BLOCKS} consecutive block signals; "
                    "stopping for this run (not retrying or working around)")
        except ParseError as exc:
            summary["failed"] += 1
            log(plat, rec.product_url, "parse_error", str(exc))
        except Exception as exc:  # network error etc.: one bad URL must not kill the run
            summary["failed"] += 1
            log(plat, rec.product_url, "http_error", f"{type(exc).__name__}: {exc}")

    with engine.begin() as conn:
        if rows:
            conn.execute(insert(price_snapshots), rows)      # append-only
        if logs:
            conn.execute(insert(scrape_log), logs)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--products", default="data/products.csv")
    ap.add_argument("--platform", action="append", help="limit to platform(s)")
    ap.add_argument("--limit", type=int, help="max products per platform (for testing)")
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config()
    products = pd.read_csv(args.products, dtype=str).dropna(subset=["platform", "product_url"])
    unknown = set(products["platform"]) - set(PARSERS)
    if unknown:
        raise SystemExit(f"products.csv has platforms with no parser: {sorted(unknown)}")
    if products.empty:
        raise SystemExit(f"{args.products} has no products. Curate 80-150 product URLs by hand first "
                         "(see data/products.example.csv).")
    ua = check_gates(cfg)
    print(f"{len(products)} products | UA: {ua}")
    if args.dry_run:
        print("Dry run OK - nothing fetched.")
        return 0
    delay = cfg.get("rate_limit", {})
    fetcher = PoliteFetcher(user_agent=ua, min_delay=delay.get("min_seconds", 3),
                            max_delay=delay.get("max_seconds", 6))
    engine = get_engine(args.database_url)
    init_db(engine)
    s = run(products, cfg, fetcher, engine, platforms=args.platform, limit=args.limit)
    print(s)
    if s["ok"] == 0 and s["failed"] > 0:
        print("No product was scraped successfully.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
