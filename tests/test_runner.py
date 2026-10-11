import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from pricing.db import init_db
from scraper import run_scrape as rs
from scraper.common import Blocked, RobotsDisallowed

HTML = '<script type="application/ld+json">{"@type":"Product","name":"N","offers":{"price":"500"}}</script>'


class Fetcher:
    def __init__(self, behaviour):
        self.behaviour, self.seen = behaviour, []

    def get(self, url):
        self.seen.append(url)
        b = self.behaviour(url)
        if isinstance(b, Exception):
            raise b
        return type("R", (), {"text": b})()


def products(n_f=3, n_a=0):
    rows = [("flipkart", f"https://f/p{i}", f"F{i}", "Br") for i in range(n_f)]
    rows += [("amazon", f"https://a/p{i}", f"A{i}", "Br") for i in range(n_a)]
    return pd.DataFrame(rows, columns=["platform", "product_url", "product_name", "brand"])


def engine():
    e = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    init_db(e)
    return e


CFG = {"platforms": {"flipkart": {"enabled": True}, "amazon": {"enabled": True}}}


def count(e, t):
    with e.connect() as c:
        return c.execute(text(f"select count(*) from {t}")).scalar()


def test_one_bad_url_does_not_kill_the_run():
    def beh(url):
        return "<html>broken layout</html>" if url.endswith("p1") else HTML
    e = engine()
    s = rs.run(products(3), CFG, Fetcher(beh), e)
    assert (s["ok"], s["failed"]) == (2, 1)
    assert count(e, "price_snapshots") == 2 and count(e, "scrape_log") == 3


def test_platform_halts_after_consecutive_blocks_and_other_platform_continues():
    def beh(url):
        return Blocked("HTTP 429") if "//a/" in url else HTML
    f = Fetcher(beh)
    e = engine()
    s = rs.run(products(2, 6), CFG, f, e)
    assert s["halted"] == ["amazon"] and s["ok"] == 2
    assert sum("//a/" in u for u in f.seen) == rs.MAX_CONSECUTIVE_BLOCKS     # stopped, did not hammer
    assert s["skipped"] == 3


def test_robots_disallowed_is_skipped_and_logged():
    e = engine()
    s = rs.run(products(2), CFG, Fetcher(lambda u: RobotsDisallowed(u)), e)
    assert s["ok"] == 0 and s["skipped"] == 2 and count(e, "price_snapshots") == 0


def test_curated_name_wins_and_rows_are_appended_not_overwritten():
    e = engine()
    rs.run(products(1), CFG, Fetcher(lambda u: HTML), e)
    rs.run(products(1), CFG, Fetcher(lambda u: HTML), e)
    assert count(e, "price_snapshots") == 2
    with e.connect() as c:
        assert c.execute(text("select distinct product_name from price_snapshots")).scalars().all() == ["F0"]


def test_gates_refuse_without_tos_ack_or_contact():
    with pytest.raises(SystemExit):
        rs.check_gates({"tos_reviewed": False}, {"SCRAPER_CONTACT": "a@b.c"})
    with pytest.raises(SystemExit):
        rs.check_gates({"tos_reviewed": True}, {})
    assert "a@b.c" in rs.check_gates({"tos_reviewed": True}, {"SCRAPER_CONTACT": "a@b.c"})
