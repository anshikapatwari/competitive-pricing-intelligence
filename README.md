# Competitive Pricing Intelligence (self-scraped)

An end-to-end pipeline and dashboard that answers: **how do competitors discount the same category over time, and does discount depth actually move demand — or just erode margin?**

Category: headphones / earbuds on Flipkart and Amazon.in. Audience: a pricing / category manager deciding whether to match a competitor's discount and how deep a discount must be to be worth running.

> **Status (honest):** the pipeline, analysis, tests and dashboard are built and tested. **No real market data has been collected yet** — that needs you to curate the product list and run the scraper daily for ~4 weeks. Until then the public page shows an empty state, and a clearly-labelled *synthetic* demo (`?demo=1`) shows how it will look. No result in this repo is a real finding.

```
curated products.csv ─▶ scraper (robots-aware, 3–6 s delay) ─▶ price_snapshots (append-only, SQLite/Postgres)
                                                                       │
                      GitHub Actions, daily ──────────────────────────┤
                                                                       ▼
                    pricing/clean.py → analysis.py → export.py ─▶ site/data/dashboard.json ─▶ static dashboard (GitHub Pages)
```

## Real-data analysis (Amazon India dataset)

`python -m analysis.amazon_analysis` reads `dataset/amazon.csv` (public Kaggle "Amazon Sales Dataset", one snapshot, 1,351 unique listings, 63 headphone/earbud listings) and writes `site/data/amazon_analysis.json`, which drives `site/index.html`. It covers discount depth by category, headphone brand positioning, price bands and discount-vs-rating associations (Spearman with bootstrap CIs). It is cross-sectional: it supports associations, not the effect of a price change. The time-series pipeline below has its own dashboard (`pipeline_demo/`, not published, synthetic data until real collection has run). This analysis module has no automated tests yet.

## What is implemented

| Area | What | Where |
|---|---|---|
| Scraper | Polite fetcher (robots.txt with wildcard matching, per-host randomised delay, honest UA, block detection → stop), Flipkart + Amazon.in parsers, run script with per-URL failure isolation | `scraper/` |
| Storage | `price_snapshots` (append-only) + `scrape_log`; SQLite by default, Postgres via `DATABASE_URL` | `pricing/db.py`, `sql/` |
| Cleaning | Guide's rules, each counted in a report; monotone review counts; stale/gap detection | `pricing/clean.py` |
| Analysis 1 | Discount events, per-product cadence, weekday pattern, brand typology, first-mover | `pricing/analysis.py` |
| Analysis 2 | 7-day before/after event study, OLS slope + bootstrap CI, hinge-model discount floor + permutation test, price-band / brand-tier lift, “not paying for itself” flags | `pricing/analysis.py` |
| Analysis 3 | Matched-product gaps, unexplained-gap ranking, value map, competitive response lag (censoring-aware) | `pricing/analysis.py` |
| Dashboard | 5 sections, platform/brand/date filters, light/dark, responsive, table fallbacks | `site/` |
| Notebooks | The four notebooks from the guide (thin wrappers over the tested modules) | `notebooks/` |
| Automation | Daily scrape + export + commit + Pages deploy; CI tests | `.github/workflows/` |

**Not implemented:** the search-rank / price-rank test (the scraper visits curated product pages, so it captures no rank); the Power BI `.pbix` (binary, needs Power BI Desktop — the web dashboard covers the same three pages; the DAX measures in the guide map directly onto `fact_discount_events` / the exported JSON); `memo.pdf` (needs real findings — see `docs/memo_template.md`).

## Quick start

```bash
pip install -r requirements-dev.txt
python -m pytest -q                      # 35 tests
python -m pricing.demo                   # writes SYNTHETIC site/data/demo.json
python -m pricing.export                 # real data -> site/data/dashboard.json (empty state if no DB)
cd site && python -m http.server 8000    # open http://localhost:8000  (add ?demo=1 for the synthetic preview)
```

### Collecting real data

1. **Curate `data/products.csv`** by hand: `platform,product_url,product_name,brand,match_id` (80–150 product pages; give the same `match_id` to the same model on both platforms). `python -m pricing.matching data/products.csv` suggests candidates to confirm; nothing is auto-matched. `data/products.example.csv` shows the format — its URLs are placeholders.
2. **Verify selectors.** Open a live product page and check the selectors in `scraper/scheduler_config.yml` / `scraper/amazon_scraper.py`. They ship **unverified**; parsers are tested only against hand-written fixtures.
3. **Read each site's Terms of Use.** `robots.txt` allows product pages (checked 2026-10-11), but robots.txt is not the same as permission, and both sites' terms restrict automated access. This is your decision and responsibility. The scraper refuses to run until `tos_reviewed: true` is set in `scraper/scheduler_config.yml`.
4. `cp .env.example .env`, set `SCRAPER_CONTACT` (goes in the User-Agent), then `python -m scraper.run_scrape --limit 3 --dry-run` → `--limit 3` → full run.
5. Re-run `python -m pricing.export` and open the dashboard. Run `notebooks/01_data_quality_check.ipynb` weekly.

### Deploy

1. Push to GitHub. Settings → Pages → Source: **GitHub Actions**.
2. Add repo secret `SCRAPER_CONTACT` (and optionally `DATABASE_URL` for Supabase/Neon; otherwise the SQLite file is committed back).
3. Actions → *daily-scrape* → Run workflow. It tests, scrapes, exports, commits data, and deploys `site/`.

A static site means no backend to host; the dashboard is fully driven by one JSON file.

## Ethics & legality (the specific answer)

robots.txt is fetched and enforced on every URL by a wildcard-aware matcher (urllib's parser ignores `*`/`$`, which both sites use); unreachable robots.txt ⇒ don't scrape. One sequential request at a time, 3–6 s random delay per host, no retries. Public product pages only; no logins, CAPTCHAs or paywalls. Honest User-Agent with a contact. Three consecutive block signals halt that platform — document and drop it rather than fight. Only product-level public data is stored. Terms-of-Use review is a hard gate in the runner, not a footnote.

## Method decisions worth being able to defend

- **Events, not snapshots.** Analysis runs on discount *changes*; the first snapshot of each product is a flagged baseline, never counted as a change.
- **Window excludes the ambiguous interval.** The change happened between the last pre-discount snapshot and the first discounted one, so "before" = 7 days ending at the former, "after" = 7 days starting at the latter. (A test caught this: anchoring at the detection time leaked the change into "before".)
- **Outages are not "no reviews".** Events are skipped when snapshots don't cover both windows or have a >3-day internal gap.
- **Contaminated windows are flagged**, not hidden (`overlap`); the dashboard fades them and reports the clean-window count.
- **Floor = hinge model** (flat below *t*, linear above), on relative lift so popular and niche products are comparable. The p-value is a permutation test on the best-of-all-thresholds statistic, so it isn't inflated by searching for *t*. With a few dozen events treat it as indicative.
- **Missing MRP is flagged**, not silently 0%: otherwise a broken selector would look like "no discounts".
- **Right-censoring** in response lag: a price cut with no follow-up inside the horizon is excluded unless a full window was observed.
- **review_count holds the number of *ratings*** (more granular than written reviews, so a better demand signal). Document this in any write-up.

## Testing — what was actually run

`python -m pytest -q` → **35 passed** (Windows, Python 3.11). Covers: robots wildcard/longest-match rules, rate limiting, UA, block detection, value parsers, both parsers on fixtures, runner failure isolation / platform halting / append-only writes / ToS gate, every cleaning rule, event detection, a hand-computed event-study answer, outage handling, SQL view = pandas parity (SQLite), and recovery of a *planted* demand response from synthetic data (slope > 0, CI excludes 0, floor within 5 pp of the planted 25%, and no "significant" floor in a null world).

Also run: the four notebooks' code against synthetic data (smoke check, `scripts/make_notebooks.py --execute-check`); the dashboard rendered in headless Chrome (all five sections, empty state, ~500 px viewport).

**Not run / unverified:** live scraping of Flipkart or Amazon (`data/products.csv` has 80 curated product URLs across 14 confirmed cross-platform matched pairs, but the scraper has not yet been run against live pages); `sql/03_postgres_views.sql` and the Postgres path (no Postgres instance); the GitHub Actions workflows (need a repo); dark mode visually; accessibility audit beyond labels/contrast tokens; notebooks were not executed in Jupyter.

## Limitations

Review velocity is a lagged, noisy proxy — not sales. Estimates are correlational (seasonality, competitor moves and launches can drive both discount and demand); a randomised discount test across comparable SKUs is the real answer. A 3–4 week window cannot capture a sale-event cycle. Cross-platform matching is manual and can mis-match bundles/regional SKUs (production: match on EAN/UPC). Event time has up-to-one-scrape-interval uncertainty. Brand typology cut-offs (10% depth, 1 change/product, 8 pp std) are judgement calls exposed in `analysis.brand_typology`.

## Interview cheat-sheet

- *“Is this legal?”* — the section above; plus the honest caveat that robots.txt ≠ ToS permission, which is why the gate exists.
- *“How do you know the scraper was right?”* — QC notebook: stale products, impossible values, duplicates, MRP-assumed share by day, scrape log by status. Have one real break you caught.
- *“Isn’t review velocity a weak proxy?”* — Yes; before/after on each product removes baseline popularity but not seasonality; I’d want unit sales or cart-adds, and a randomised test.
- *“How do you find the discount floor?”* — hinge model + permutation test; point to the null-world test.
- *“Why not Kaggle?”* — nothing in a CSV shows pipeline, live data quality or the legal judgement call.

## Remaining before this is “done”

1. ~~Curate `data/products.csv` (80–150 URLs)~~ — **done** (80 products, 14 matched pairs). Confirm selectors on a live page before the first run.
2. Review Terms of Use; set `tos_reviewed: true`.
3. Collect ≥ 4 weeks; fix what breaks (and write it down).
4. Replace the placeholder repo link in `site/index.html`, add screenshots, write `memo.pdf` from `docs/memo_template.md`, fill resume bullets only from real numbers.
