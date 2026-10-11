"""Flipkart product-page parser.

Strategy, most to least stable:
  1. schema.org JSON-LD <script type="application/ld+json"> Product  -> price, rating, count, stock
  2. CSS selectors from scheduler_config.yml (flipkart.mrp_selectors etc.)

Flipkart's class names are auto-generated and change without notice, so selectors live in
config, not code. They ship UNVERIFIED: open a live product page, inspect it, and update
scraper/scheduler_config.yml before the first real run. A selector break shows up in the
data-quality report as a jump in `mrp_assumed_equal_price` / parse errors.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

from .common import (ParseError, build_row, first_text, jsonld_product, parse_float,
                     parse_int, parse_price)

PLATFORM = "flipkart"
DEFAULT_SELECTORS = {
    "price_selectors": ["div.Nx9bqj", "div._30jeq3"],
    "mrp_selectors": ["div.yRaY8j", "div._3I9_wc"],
    "rating_selectors": ["div.XQDdHH", "div._3LWZlK"],
    "review_count_selectors": ["span.Wphh3N", "span._2_R_DZ"],
    "title_selectors": ["span.VU-ZEz", "span.B_NuCI", "h1"],
}


def parse_flipkart(html: str, url: str, selectors: dict | None = None, now=None) -> dict:
    sel = {**DEFAULT_SELECTORS, **(selectors or {})}
    soup = BeautifulSoup(html, "html.parser")
    notes: list[str] = []
    name = brand = price = mrp = rating = reviews = None
    in_stock = None

    prod = jsonld_product(soup)
    if prod:
        name = prod.get("name")
        b = prod.get("brand")
        brand = b.get("name") if isinstance(b, dict) else b
        offers = prod.get("offers")
        offer = offers[0] if isinstance(offers, list) and offers else offers
        if isinstance(offer, dict):
            price = parse_price(offer.get("price") or offer.get("lowPrice"))
            avail = str(offer.get("availability", ""))
            in_stock = avail.endswith("InStock") if avail else None
            specs = offer.get("priceSpecification")
            for spec in (specs if isinstance(specs, list) else [specs] if specs else []):
                kind = str(spec.get("priceType", "")).lower()
                if any(k in kind for k in ("list", "strikethrough", "msrp", "mrp")):
                    mrp = parse_price(spec.get("price"))
        agg = prod.get("aggregateRating")
        if isinstance(agg, dict):
            rating = parse_float(agg.get("ratingValue"))
            # Count of ratings (not written reviews): accumulates faster => better demand signal.
            reviews = parse_int(agg.get("ratingCount") or agg.get("reviewCount"))
    else:
        notes.append("no_jsonld")

    if price is None:
        price = parse_price(first_text(soup, sel["price_selectors"]))
        if price is not None:
            notes.append("price_from_css")
    if mrp is None:
        mrp = parse_price(first_text(soup, sel["mrp_selectors"]))
        if mrp is not None and price is not None and mrp < price:
            mrp = None  # a struck price below the selling price is a mis-selected element
    if rating is None:
        rating = parse_float(first_text(soup, sel["rating_selectors"]))
    if reviews is None:
        reviews = parse_int(first_text(soup, sel["review_count_selectors"]))
    if name is None:
        el = first_text(soup, sel["title_selectors"])
        name = el.get_text(" ", strip=True) if el else None
    if price is None:
        raise ParseError("flipkart: selling price not found (layout change?)")
    return build_row(PLATFORM, url, name, brand, mrp, price, rating, reviews, in_stock, notes, now)
