-- Portable views (tested on SQLite in tests/test_sql_views.py; valid PostgreSQL).
-- They apply only the *minimum* validity filter; the full cleaning rules live in
-- pricing/clean.py, which is what the analysis and dashboard use.

DROP VIEW IF EXISTS valid_snapshots;
CREATE VIEW valid_snapshots AS
SELECT *
FROM price_snapshots
WHERE selling_price > 0 AND mrp > 0 AND selling_price <= mrp;

DROP VIEW IF EXISTS dim_products;
CREATE VIEW dim_products AS            -- one row per product_url, never keyed on product_name
SELECT product_url,
       MIN(platform)       AS platform,
       MAX(brand)          AS brand,
       MAX(product_name)   AS product_name,
       MIN(scraped_at)     AS first_seen,
       MAX(scraped_at)     AS last_seen,
       COUNT(*)            AS n_snapshots,
       AVG(selling_price)  AS avg_price,
       AVG(rating)         AS avg_rating,
       MAX(review_count)   AS latest_review_count
FROM valid_snapshots
GROUP BY product_url;

DROP VIEW IF EXISTS fact_discount_events;
CREATE VIEW fact_discount_events AS    -- a row only when discount_pct changes (plus each baseline)
WITH ordered AS (
  SELECT s.*,
         LAG(discount_pct) OVER (PARTITION BY product_url ORDER BY scraped_at) AS prev_discount,
         LAG(scraped_at)   OVER (PARTITION BY product_url ORDER BY scraped_at) AS prev_scraped_at
  FROM valid_snapshots s
)
SELECT product_url, platform, brand, product_name, scraped_at, prev_scraped_at,
       prev_discount, discount_pct, selling_price, mrp,
       (prev_discount IS NULL)               AS is_baseline,
       (discount_pct - prev_discount)        AS discount_change
FROM ordered
WHERE prev_discount IS NULL OR discount_pct <> prev_discount;
