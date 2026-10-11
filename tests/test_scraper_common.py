from types import SimpleNamespace

import pytest

from scraper.common import (Blocked, PoliteFetcher, RobotsDisallowed, RobotsRules,
                            parse_float, parse_int, parse_price)

ROBOTS = """
User-agent: *
Disallow: /viewcart
Disallow: /*?sizeSelected=
Disallow: */product-questions*
Allow: /viewcart/public$

User-agent: student-pricing-project
Disallow: /private
"""


def test_robots_wildcards_and_longest_match():
    # our UA has its own group, so '*' rules do not apply to us
    r = RobotsRules(ROBOTS, "student-pricing-project/1.0 (+contact: a@b.c)")
    assert r.allowed("/viewcart") and not r.allowed("/private/x")
    star = RobotsRules(ROBOTS, "otherbot/1.0")
    assert not star.allowed("/viewcart")
    assert star.allowed("/viewcart/public")            # longer Allow beats shorter Disallow
    assert not star.allowed("/p/x?sizeSelected=M")      # '*' wildcard
    assert not star.allowed("/a/b/product-questions/1")
    assert star.allowed("/some-product/p/itm123")


def test_empty_disallow_allows_everything():
    assert RobotsRules("User-agent: *\nDisallow:\n", "x/1").allowed("/anything")


class FakeSession:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, headers))
        status, text = self.pages.get(url, (404, ""))

        def raise_for_status():
            if status >= 400:
                raise RuntimeError(status)

        return SimpleNamespace(status_code=status, text=text, headers={"content-type": "text/html"},
                               raise_for_status=raise_for_status)


def make_fetcher(pages):
    sleeps = []
    t = {"now": 0.0}

    def sleep(s):
        sleeps.append(s)
        t["now"] += s

    f = PoliteFetcher(user_agent="student-pricing-project/1.0 (+contact: t@t.t)",
                      session=FakeSession(pages), sleep=sleep,
                      clock=lambda: t["now"], uniform=lambda a, b: b)
    return f, sleeps


def test_fetcher_respects_robots_and_sends_honest_ua():
    pages = {"https://x.test/robots.txt": (200, "User-agent: *\nDisallow: /nope"),
             "https://x.test/ok": (200, "<html>fine</html>")}
    f, _ = make_fetcher(pages)
    assert f.get("https://x.test/ok").text == "<html>fine</html>"
    with pytest.raises(RobotsDisallowed):
        f.get("https://x.test/nope/1")
    assert all("student-pricing-project" in h["User-Agent"] for _, h in f.session.calls)
    assert "https://x.test/nope/1" not in [u for u, _ in f.session.calls]


def test_fetcher_rate_limits_per_host():
    pages = {"https://x.test/robots.txt": (404, ""), "https://x.test/a": (200, "a"),
             "https://x.test/b": (200, "b")}
    f, sleeps = make_fetcher(pages)
    f.get("https://x.test/a")
    f.get("https://x.test/b")
    assert len(sleeps) >= 2 and all(s >= 3 - 1e-9 for s in sleeps)   # robots->a and a->b both paced


@pytest.mark.parametrize("status,body", [(429, ""), (403, ""), (200, "Enter the characters you see below")])
def test_block_signals_raise_blocked(status, body):
    pages = {"https://x.test/robots.txt": (404, ""), "https://x.test/p": (status, body)}
    f, _ = make_fetcher(pages)
    with pytest.raises(Blocked):
        f.get("https://x.test/p")


def test_unreachable_robots_is_conservative():
    f, _ = make_fetcher({"https://x.test/robots.txt": (503, "")})
    with pytest.raises(RobotsDisallowed):
        f.get("https://x.test/p")


def test_value_parsers():
    assert parse_price("₹1,499") == 1499 and parse_price("Rs. 2,999.50") == 2999.5
    assert parse_price("free") is None and parse_price(None) is None and parse_price("0") is None
    assert parse_int("12,043 ratings") == 12043 and parse_int("1.2K") == 1200
    assert parse_int("3.4 lakh") == 340000 and parse_int("") is None
    assert parse_float("4.3 out of 5 stars") == 4.3
