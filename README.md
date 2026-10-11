# Competitive Pricing Intelligence: Amazon India Headphones and Earbuds

An analysis of 1,351 Amazon.in listings, with a focus on headphones and earbuds. It answers three questions a pricing or category manager would ask:

1. How deeply do different categories, and headphone brands in particular, discount against their list price (MRP)?
2. How do brands position themselves on price, discount and customer rating?
3. Do more heavily discounted listings have more customer ratings?

**Live dashboard:** https://amazon-pricing-analysis.netlify.app/

The results are published as a static dashboard in `site/`.

## Data

| | |
|---|---|
| Source | Public Kaggle dataset "Amazon Sales Dataset" (Amazon.in listings) |
| Snapshot date | 2023-01-05, derived from the timestamp embedded in the product links |
| Rows | 1,465 raw, 1,351 unique products after removing repeated product IDs |
| Headphone, earbud and accessory listings | 63 |
| Fields used | selling price, MRP, discount %, rating, rating count, category path, product title |

The raw CSV is not committed. Download `amazon.csv` from the Kaggle dataset page and place it at `dataset/amazon.csv`. The dataset is third-party data and is credited as such on the site.

## Findings

All figures are computed by `analysis/amazon_analysis.py` and appear on the dashboard.

- Headphones, earbuds and accessories have a median discount of 63%, ranking second of 13 sub-categories with at least 15 listings.
- 62% of headphone listings are discounted by 60% or more, against 37% elsewhere in Electronics. Deep discounts are the norm, so the discount percentage alone is a weak signal of value.
- Among brands with at least three listings, Zebronics has the lowest median selling price (₹449) and Noise the highest (₹1,299). Boult discounts deepest, with a median of 78%.
- Within headphones, the rank correlation between discount depth and number of ratings is -0.23 (95% bootstrap interval -0.45 to -0.01, n = 63). Across all products it is -0.13 (interval -0.18 to -0.08, n = 1,349).

The negative correlation is consistent with discounts being applied to less popular items. It is an association within one snapshot and is not evidence that discounting reduces or increases demand.

## Method

- **Cleaning.** Currency symbols and thousands separators are stripped, discount percentages are parsed from text, repeated product IDs are collapsed, and the stated discount is checked against the value recomputed from price and MRP (no mismatches over 1 percentage point were found).
- **Statistics.** Medians are used because price and rating-count distributions are skewed. Associations use Spearman rank correlation with 95% intervals from a bootstrap of 2,000 resamples. Rating count is log-transformed. Correlations are also computed within sub-categories to avoid mixing categories that differ in both discount level and popularity.
- **Brands.** Brand is taken from the first word of the product title and normalised for spelling. Brands with fewer than three headphone listings are grouped as "Other brands".

## Limitations

- **Small headphone sample.** 63 listings, so brand-level figures rest on a handful of products each.
- **Accessories included.** The headphone sub-category also contains adapters, cases and ear pads.
- **One platform, one date.** There is no Flipkart comparison and no time series, so nothing here shows the effect of a price change.
- **MRP is seller-reported.** A high discount may reflect an inflated list price.
- **Rating count is not sales.** It is cumulative over the product's lifetime.
- The analysis module has no automated tests yet.

## Run it

```bash
pip install -r requirements.txt
python -m analysis.amazon_analysis         # reads dataset/amazon.csv, writes site/data/amazon_analysis.json
cd site && python -m http.server 8000      # open http://localhost:8000
```

## Deploy

The live site is hosted on Netlify (publish directory `site`, no build command), connected to this repository so every push to `main` redeploys. As an alternative, Settings, Pages, Source: **GitHub Actions** enables the `deploy-site` workflow, which also publishes the `site/` folder.

## Repository layout

| Path | Contents |
|---|---|
| `analysis/amazon_analysis.py` | Cleaning and analysis of the Amazon dataset, writes the dashboard JSON |
| `site/` | Static dashboard (`index.html`, `analysis.js`, `styles.css`, `data/amazon_analysis.json`) |
| `scraper/`, `pricing/`, `sql/`, `tests/` | Optional live price-collection pipeline (see below) and its 35 tests |
| `data/products.csv` | 80 curated Flipkart and Amazon.in product URLs, with 14 cross-platform matched pairs |
| `pipeline_demo/` | Time-series dashboard for the pipeline, populated with synthetic data and not published |
| `notebooks/`, `docs/` | Notebooks and a memo template for the pipeline |

## Optional: live price-collection pipeline

The repository also contains a scraper and time-series analysis for Flipkart and Amazon.in. It was not used for any figure on the dashboard, and no live data has been collected with it.

- Fetcher: robots.txt enforced with wildcard matching, randomised 3 to 6 second delay per host, descriptive User-Agent, and a halt after three consecutive block responses.
- Storage: append-only price snapshots in SQLite, or Postgres through `DATABASE_URL`.
- Analysis: discount events, a seven-day before-and-after event study, and a hinge-model discount floor with a permutation test.
- Tests: `python -m pytest -q` runs 35 tests, including recovery of a planted demand effect from synthetic data.
- Unverified: the Flipkart and Amazon CSS selectors have not been checked against live pages, and the scraper has never been run against live sites.
- Terms of Use: the scraper will not run until `tos_reviewed: true` is set in `scraper/scheduler_config.yml`, which requires reading each site's Terms of Use. The daily GitHub Actions schedule is disabled and can be started manually.

To preview the pipeline dashboard with synthetic data:

```bash
python -m pricing.demo --out pipeline_demo/data/dashboard.json
python -m http.server 8001                       # from the repo root; open http://localhost:8001/pipeline_demo/pipeline.html
```

## Possible extensions

1. Add a dated Flipkart versus Amazon price snapshot for the 14 matched pairs in `data/products.csv`.
2. Verify the scraper selectors and collect several weeks of data to study price changes over time.
3. Add automated tests for `analysis/amazon_analysis.py`.
