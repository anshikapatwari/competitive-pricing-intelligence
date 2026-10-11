"""Database access. SQLite by default; set DATABASE_URL for Postgres (Supabase/Neon)."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from sqlalchemy import (Boolean, Column, DateTime, Integer, MetaData, Numeric,
                        Table, Text, create_engine)
from sqlalchemy.engine import Engine

DEFAULT_URL = "sqlite:///data/pricing.db"

metadata = MetaData()

price_snapshots = Table(
    "price_snapshots", metadata,
    Column("snapshot_id", Integer, primary_key=True, autoincrement=True),
    Column("platform", Text),
    Column("product_url", Text),
    Column("product_name", Text),
    Column("brand", Text),
    Column("mrp", Numeric(12, 2, asdecimal=False)),
    Column("selling_price", Numeric(12, 2, asdecimal=False)),
    Column("discount_pct", Numeric(6, 2, asdecimal=False)),
    Column("rating", Numeric(3, 2, asdecimal=False)),
    Column("review_count", Integer),
    Column("in_stock", Boolean),
    Column("scraped_at", DateTime(timezone=True)),
    Column("parse_notes", Text),
)

scrape_log = Table(
    "scrape_log", metadata,
    Column("log_id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", Text),
    Column("platform", Text),
    Column("product_url", Text),
    Column("status", Text),       # ok | parse_error | blocked | robots_disallowed | http_error | platform_halted
    Column("detail", Text),
    Column("http_status", Integer),
    Column("logged_at", DateTime(timezone=True)),
)


def get_engine(url: str | None = None) -> Engine:
    url = url or os.environ.get("DATABASE_URL") or DEFAULT_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, future=True)


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)


def load_snapshots(engine: Engine) -> pd.DataFrame:
    df = pd.read_sql_table("price_snapshots", engine)
    df["scraped_at"] = pd.to_datetime(df["scraped_at"], utc=True)
    return df


def load_scrape_log(engine: Engine) -> pd.DataFrame:
    df = pd.read_sql_table("scrape_log", engine)
    df["logged_at"] = pd.to_datetime(df["logged_at"], utc=True)
    return df
