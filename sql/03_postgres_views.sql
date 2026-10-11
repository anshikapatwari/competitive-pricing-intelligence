-- PostgreSQL-only (uses EXTRACT(EPOCH ...)). NOT executed in this repo's test-suite:
-- no Postgres instance was available when this was written. Run it against yours.

DROP VIEW IF EXISTS review_velocity;
CREATE VIEW review_velocity AS         -- demand proxy: new ratings per day between snapshots
WITH ordered AS (
  SELECT product_url, scraped_at,
         MAX(review_count) OVER (PARTITION BY product_url ORDER BY scraped_at
                                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS review_count,  -- monotone: decreases are noise
         LAG(scraped_at) OVER (PARTITION BY product_url ORDER BY scraped_at) AS prev_at
  FROM valid_snapshots
), d AS (
  SELECT *, LAG(review_count) OVER (PARTITION BY product_url ORDER BY scraped_at) AS prev_count
  FROM ordered
)
SELECT product_url, scraped_at,
       (review_count - prev_count) AS review_delta,
       (review_count - prev_count) / NULLIF(EXTRACT(EPOCH FROM (scraped_at - prev_at)) / 86400.0, 0) AS velocity_per_day
FROM d
WHERE prev_at IS NOT NULL;
