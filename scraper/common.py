"""Shared scraping utilities: polite fetching, robots.txt enforcement, value parsers."""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup


class RobotsDisallowed(Exception):
    """The site's robots.txt forbids fetching this URL for our user agent."""


class Blocked(Exception):
    """The site answered with a block / CAPTCHA signal. We stop; we do not work around it."""


class ParseError(Exception):
    """The page loaded but the price could not be extracted (layout change?)."""


# ----------------------------------------------------------------------- robots.txt
class RobotsRules:
    """Minimal RFC 9309 matcher. Python's urllib.robotparser ignores `*` and `$` wildcards,
    and both Flipkart and Amazon rely on them, so we match ourselves.
    Longest matching pattern wins; Allow beats Disallow on a tie."""

    def __init__(self, text: str, agent: str):
        self.rules: list[tuple[bool, re.Pattern, int]] = []
        groups: dict[str, list[tuple[bool, str]]] = {}
        current: list[str] = []
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key, val = key.strip().lower(), val.strip()
            if key == "user-agent":
                if not last_was_agent:
                    current = []
                current.append(val.lower())
                groups.setdefault(val.lower(), [])
                last_was_agent = True
            elif key in ("allow", "disallow"):
                last_was_agent = False
                for a in current:
                    groups[a].append((key == "allow", val))
        token = agent.split("/")[0].lower()
        chosen = next((groups[a] for a in groups if a != "*" and a in token), None)
        if chosen is None:
            chosen = groups.get("*", [])
        for allow, pat in chosen:
            if pat == "":
                continue  # "Disallow:" with empty value = allow everything
            anchored = pat.endswith("$")
            body = re.escape(pat.rstrip("$")).replace(r"\*", ".*")
            self.rules.append((allow, re.compile("^" + body + ("$" if anchored else "")), len(pat)))

    def allowed(self, path_and_query: str) -> bool:
        best_len, verdict = -1, True
        for allow, rx, ln in self.rules:
            if rx.match(path_and_query) and (ln > best_len or (ln == best_len and allow)):
                best_len, verdict = ln, allow
        return verdict


# ------------------------------------------------------------------- polite fetcher
@dataclass
class PoliteFetcher:
    """One request at a time, a randomised 3-6 s pause per host, robots.txt checked first,
    honest User-Agent, no retries, no proxies, no CAPTCHA handling."""

    user_agent: str
    min_delay: float = 3.0
    max_delay: float = 6.0
    timeout: float = 15.0
    session: requests.Session = field(default_factory=requests.Session)
    sleep: callable = time.sleep
    clock: callable = time.monotonic
    uniform: callable = random.uniform
    _last: dict = field(default_factory=dict)
    _robots: dict = field(default_factory=dict)

    BLOCK_STATUS = (403, 429, 503)
    BLOCK_MARKERS = ("enter the characters you see below", "api-services-support@amazon.com",
                     "robot check", "are you a human", "/captcha/", "unusual traffic")

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            need = self.uniform(self.min_delay, self.max_delay) - (self.clock() - last)
            if need > 0:
                self.sleep(need)
        self._last[host] = self.clock()

    def _raw_get(self, url: str) -> requests.Response:
        self._wait(urlsplit(url).netloc)
        resp = self.session.get(url, headers={"User-Agent": self.user_agent}, timeout=self.timeout)
        self._last[urlsplit(url).netloc] = self.clock()
        return resp

    def robots_for(self, url: str) -> RobotsRules:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            try:
                r = self._raw_get(f"{host}/robots.txt")
            except requests.RequestException:
                text = "User-agent: *\nDisallow: /"          # unreachable: be conservative
            else:
                if r.status_code >= 500:
                    text = "User-agent: *\nDisallow: /"
                elif r.status_code >= 400:
                    text = ""                                  # no robots.txt: nothing disallowed
                else:
                    text = r.text
            self._robots[host] = RobotsRules(text, self.user_agent)
        return self._robots[host]

    def get(self, url: str) -> requests.Response:
        parts = urlsplit(url)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
        if not self.robots_for(url).allowed(target):
            raise RobotsDisallowed(url)
        resp = self._raw_get(url)
        body = resp.text[:20000].lower() if resp.headers.get("content-type", "").startswith("text") else ""
        if resp.status_code in self.BLOCK_STATUS or any(m in body for m in self.BLOCK_MARKERS):
            raise Blocked(f"HTTP {resp.status_code}")
        resp.raise_for_status()
        return resp


# ----------------------------------------------------------------------- value parsers
def parse_price(text) -> float | None:
    """'₹1,499' / 'Rs. 1,499.00' / '1499' -> 1499.0.  Takes the first number in the string."""
    if text is None:
        return None
    if not isinstance(text, str):
        text = text.get_text(" ", strip=True) if hasattr(text, "get_text") else str(text)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if not m:
        return None
    v = float(m.group(0).replace(",", ""))
    return v if v > 0 else None


def parse_float(text) -> float | None:
    if text is None:
        return None
    if not isinstance(text, str):
        text = text.get_text(" ", strip=True) if hasattr(text, "get_text") else str(text)
    m = re.search(r"\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else None


def parse_int(text) -> int | None:
    """'12,043 ratings' -> 12043 ; '1.2K' -> 1200 ; '3.4 lakh' -> 340000."""
    if text is None:
        return None
    if not isinstance(text, str):
        text = text.get_text(" ", strip=True) if hasattr(text, "get_text") else str(text)
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k|lakh|lac|m)?\b", text.lower())
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    mult = {"k": 1e3, "lakh": 1e5, "lac": 1e5, "m": 1e6}.get(m.group(2) or "", 1)
    return int(round(v * mult))


def jsonld_product(soup: BeautifulSoup) -> dict | None:
    """First schema.org Product object found in any <script type=application/ld+json>."""
    def walk(node):
        if isinstance(node, list):
            for n in node:
                yield from walk(n)
        elif isinstance(node, dict):
            t = node.get("@type")
            if t == "Product" or (isinstance(t, list) and "Product" in t):
                yield node
            for v in node.get("@graph", []) if isinstance(node.get("@graph"), list) else []:
                yield from walk(v)

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (ValueError, TypeError):
            continue
        for prod in walk(data):
            return prod
    return None


def first_text(soup: BeautifulSoup, selectors: list[str]):
    for sel in selectors:
        el = soup.select_one(sel)
        if el is not None and el.get_text(strip=True):
            return el
    return None


def build_row(platform: str, url: str, name, brand, mrp, price, rating, reviews, in_stock,
              notes: list[str], now: datetime | None = None) -> dict:
    if price is None:
        raise ParseError("selling price not found")
    if mrp is None:
        # No struck-through price on the page => not discounted. Flagged so a selector that
        # silently broke (which would look like "0% discount everywhere") shows up in QC.
        mrp = price
        notes.append("mrp_assumed_equal_price")
    now = now or datetime.now(timezone.utc)
    return {
        "platform": platform, "product_url": url, "product_name": name, "brand": brand,
        "mrp": mrp, "selling_price": price,
        "discount_pct": round((mrp - price) / mrp * 100, 2) if mrp else None,
        "rating": rating, "review_count": reviews, "in_stock": in_stock,
        "scraped_at": now, "parse_notes": ";".join(notes) or None,
    }
