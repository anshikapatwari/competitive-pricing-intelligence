"""Parser tests run on small HAND-WRITTEN fixtures that mimic the documented page structure.
They prove the parsing logic, NOT that the selectors match today's live Flipkart/Amazon pages."""
import json
from datetime import datetime, timezone

import pytest

from scraper.amazon_scraper import parse_amazon
from scraper.common import ParseError
from scraper.flipkart_scraper import parse_flipkart

NOW = datetime(2026, 10, 18, 3, 30, tzinfo=timezone.utc)
LD = {"@context": "https://schema.org", "@type": "Product", "name": "Acme Buds Pro",
      "brand": {"@type": "Brand", "name": "Acme"},
      "offers": {"@type": "Offer", "price": "1499", "availability": "https://schema.org/InStock",
                 "priceSpecification": [{"priceType": "https://schema.org/ListPrice", "price": "2999"}]},
      "aggregateRating": {"ratingValue": "4.3", "ratingCount": "12043", "reviewCount": "900"}}


def fk_html(ld=LD, body=""):
    s = f'<script type="application/ld+json">{json.dumps(ld)}</script>' if ld else ""
    return f"<html><head>{s}</head><body>{body}</body></html>"


def test_flipkart_jsonld():
    r = parse_flipkart(fk_html(), "https://f/p", now=NOW)
    assert (r["mrp"], r["selling_price"], r["discount_pct"]) == (2999, 1499, 50.02)
    assert (r["rating"], r["review_count"], r["in_stock"], r["brand"]) == (4.3, 12043, True, "Acme")
    assert r["parse_notes"] is None


def test_flipkart_css_fallback_and_mrp_guard():
    ld = {k: v for k, v in LD.items() if k not in ("offers", "aggregateRating")}
    html = fk_html(ld, '<div class="Nx9bqj">₹1,299</div><div class="yRaY8j">₹1,999</div>'
                       '<div class="XQDdHH">4.1</div><span class="Wphh3N">2,345 Ratings</span>')
    r = parse_flipkart(html, "u", now=NOW)
    assert (r["mrp"], r["selling_price"], r["rating"], r["review_count"]) == (1999, 1299, 4.1, 2345)
    assert "price_from_css" in r["parse_notes"]
    # an "MRP" lower than the price is a mis-selected element -> treated as missing
    bad = fk_html(ld, '<div class="Nx9bqj">₹1,299</div><div class="yRaY8j">₹99</div>')
    assert parse_flipkart(bad, "u", now=NOW)["mrp"] == 1299


def test_no_mrp_is_flagged_not_silent():
    ld = {**LD, "offers": {"price": "999", "availability": "InStock"}}
    r = parse_flipkart(fk_html(ld), "u", now=NOW)
    assert r["discount_pct"] == 0.0 and "mrp_assumed_equal_price" in r["parse_notes"]


def test_flipkart_parse_error_when_no_price():
    with pytest.raises(ParseError):
        parse_flipkart("<html><body>Page not found</body></html>", "u")


AMAZON = """<html><body><span id="productTitle"> Acme Buds Pro </span>
<a id="bylineInfo">Visit the Acme Store</a>
<div id="corePrice_feature_div">
  <span class="a-price"><span class="a-offscreen">₹1,499</span></span>
  <span class="a-price a-text-price"><span class="a-offscreen">₹2,999</span></span></div>
<div id="acrPopover"><span class="a-icon-alt">4.3 out of 5 stars</span></div>
<span id="acrCustomerReviewText">12,043 ratings</span>
<div id="availability"><span>In stock</span></div></body></html>"""


def test_amazon_parse():
    r = parse_amazon(AMAZON, "https://a/dp/X", now=NOW)
    assert (r["product_name"], r["brand"]) == ("Acme Buds Pro", "Acme")
    assert (r["mrp"], r["selling_price"], r["rating"], r["review_count"], r["in_stock"]) == (
        2999, 1499, 4.3, 12043, True)


def test_amazon_out_of_stock_without_price_is_parse_error():
    with pytest.raises(ParseError):
        parse_amazon('<html><div id="availability"><span>Currently unavailable.</span></div></html>', "u")
