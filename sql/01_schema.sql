-- PostgreSQL schema. `python -m scraper.run_scrape` creates equivalent tables automatically
-- (SQLAlchemy, both SQLite and Postgres); this file is the reference DDL.

CREATE TABLE IF NOT EXISTS price_snapshots (   -- raw, append-only: one row per product per scrape
  snapshot_id    SERIAL PRIMARY KEY,
  platform       TEXT,
  product_url    TEXT,
  product_name   TEXT,
  brand          TEXT,
  mrp            NUMERIC(12,2),
  selling_price  NUMERIC(12,2),
  discount_pct   NUMERIC(6,2),
  rating         NUMERIC(3,2),
  review_count   INT,                          -- number of ratings (see README: demand proxy)
  in_stock       BOOLEAN,
  scraped_at     TIMESTAMPTZ,
  parse_notes    TEXT                          -- e.g. 'mrp_assumed_equal_price'
);
CREATE INDEX IF NOT EXISTS ix_snapshots_url_time ON price_snapshots (product_url, scraped_at);

CREATE TABLE IF NOT EXISTS scrape_log (        -- one row per attempted fetch: ok / parse_error / blocked ...
  log_id       SERIAL PRIMARY KEY,
  run_id       TEXT,
  platform     TEXT,
  product_url  TEXT,
  status       TEXT,
  detail       TEXT,
  http_status  INT,
  logged_at    TIMESTAMPTZ
);
