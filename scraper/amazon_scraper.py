"""Amazon.in product-page parser (static HTML, CSS-selector based).

Selectors are the long-lived ids/classes as of writing and ship UNVERIFIED against the live
site; confirm them on a real page and adjust scraper/scheduler_config.yml. Amazon serves
CAPTCHAs to automated clients quickly: the fetcher treats that as a hard stop (see common.py),
and per the project's ground rules we drop the platform rather than work around it.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .common import (ParseError, build_row, first_text, jsonld_product, parse_float,
                     parse_int, parse_price)

PLATFORM = "amazon"
DEFAULT_SELECTORS = {
    "price_selectors": [
        "#corePrice_feature_div span.a-price:not(.a-text-price) span.a-offscreen",
        "span.priceToPay span.a-offscreen",
        "#priceblock_ourprice", "#priceblock_dealprice",
    ],
    "mrp_selectors": [
        "#corePrice_feature_div span.a-price.a-text-price span.a-offscreen",
        "span.basisPrice span.a-offscreen",
        "#listPrice", ".a-text-strike",
    ],
    "rating_selectors": ["#acrPopover span.a-icon-alt", "span[data-hook='rating-out-of-text']"],
    "review_count_selectors": ["#acrCustomerReviewText"],
    "title_selectors": ["#productTitle"],
    "brand_selectors": ["#bylineInfo", "tr.po-brand td.a-span9 span"],
    "availability_selectors": ["#availability span", "#availability"],
}


def _brand(text: str | None) -> str | None:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^(Visit the |Brand:\s*)", "", t, flags=re.I)
    t = re.sub(r"\s+Store$", "", t, flags=re.I)
    return t or None


def parse_amazon(html: str, url: str, selectors: dict | None = None, now=None) -> dict:
    sel = {**DEFAULT_SELECTORS, **(selectors or {})}
    soup = BeautifulSoup(html, "html.parser")
    notes: list[str] = []

    price = parse_price(first_text(soup, sel["price_selectors"]))
    mrp = parse_price(first_text(soup, sel["mrp_selectors"]))
    if mrp is not None and price is not None and mrp < price:
        mrp = None
    rating = parse_float(first_text(soup, sel["rating_selectors"]))
    reviews = parse_int(first_text(soup, sel["review_count_selectors"]))
    title = first_text(soup, sel["title_selectors"])
    name = title.get_text(" ", strip=True) if title else None
    b = first_text(soup, sel["brand_selectors"])
    brand = _brand(b.get_text(" ", strip=True) if b else None)
    avail = first_text(soup, sel["availability_selectors"])
    in_stock = None
    if avail:
        txt = avail.get_text(" ", strip=True).lower()
        in_stock = "unavailable" not in txt and "out of stock" not in txt

    if price is None:                    # JSON-LD is rare on Amazon but cheap to try
        prod = jsonld_product(soup)
        offer = (prod or {}).get("offers")
        offer = offer[0] if isinstance(offer, list) and offer else offer
        if isinstance(offer, dict):
            price = parse_price(offer.get("price"))
            if price is not None:
                notes.append("price_from_jsonld")
    if price is None:
        raise ParseError("amazon: selling price not found (layout change, or out of stock?)")
    return build_row(PLATFORM, url, name, brand, mrp, price, rating, reviews, in_stock, notes, now)
