"""Tests voor sitemap/link discovery."""
import pytest

from src.discovery import _is_html_url, _parse_sitemap_xml

SAMPLE_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/getting-started</loc></url>
  <url><loc>https://docs.example.com/api/overview</loc></url>
  <url><loc>https://docs.example.com/guides/installation</loc></url>
</urlset>
"""


def test_parse_sitemap_xml_returns_urls():
    """Sitemap XML wordt correct geparsed."""
    urls = _parse_sitemap_xml(SAMPLE_SITEMAP, "https://docs.example.com")
    assert len(urls) == 3
    assert "https://docs.example.com/getting-started" in urls
    assert "https://docs.example.com/api/overview" in urls


def test_parse_sitemap_xml_filters_external_domains():
    """URLs van andere domeinen worden gefilterd."""
    with_external = SAMPLE_SITEMAP.replace(
        "</urlset>",
        "  <url><loc>https://other.com/page</loc></url>\n</urlset>",
    )
    urls = _parse_sitemap_xml(with_external, "https://docs.example.com")
    assert all("docs.example.com" in u for u in urls)
    assert "https://other.com/page" not in urls


def test_parse_sitemap_xml_empty():
    """Lege sitemap geeft lege lijst."""
    urls = _parse_sitemap_xml("<urlset></urlset>", "https://docs.example.com")
    assert urls == []


def test_parse_sitemap_xml_no_loc_tags():
    """Sitemap zonder <loc> tags geeft lege lijst."""
    urls = _parse_sitemap_xml("<urlset><url></url></urlset>", "https://docs.example.com")
    assert urls == []


def test_parse_sitemap_xml_filters_non_html():
    """Niet-HTML URLs (afbeeldingen, JS, CSS, fonts) worden gefilterd uit sitemap."""
    sitemap = """<?xml version="1.0" encoding="UTF-8"?>
<urlset>
  <url><loc>https://docs.example.com/guide</loc></url>
  <url><loc>https://docs.example.com/logo.png</loc></url>
  <url><loc>https://docs.example.com/style.css</loc></url>
  <url><loc>https://docs.example.com/app.js</loc></url>
  <url><loc>https://docs.example.com/font.woff2</loc></url>
  <url><loc>https://docs.example.com/spec.pdf</loc></url>
</urlset>"""
    urls = _parse_sitemap_xml(sitemap, "https://docs.example.com")
    assert urls == ["https://docs.example.com/guide"]


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://docs.example.com/guide", True),
        ("https://docs.example.com/api/v1", True),
        ("https://docs.example.com/page.html", True),
        ("https://docs.example.com/logo.png", False),
        ("https://docs.example.com/logo.jpg", False),
        ("https://docs.example.com/logo.jpeg", False),
        ("https://docs.example.com/logo.gif", False),
        ("https://docs.example.com/logo.webp", False),
        ("https://docs.example.com/icon.svg", False),
        ("https://docs.example.com/favicon.ico", False),
        ("https://docs.example.com/style.css", False),
        ("https://docs.example.com/app.js", False),
        ("https://docs.example.com/app.mjs", False),
        ("https://docs.example.com/font.woff", False),
        ("https://docs.example.com/font.woff2", False),
        ("https://docs.example.com/font.ttf", False),
        ("https://docs.example.com/font.eot", False),
        ("https://docs.example.com/spec.pdf", False),
        ("https://docs.example.com/data.zip", False),
        ("https://docs.example.com/sitemap.xml", False),
        ("https://docs.example.com/openapi.json", False),
        ("https://docs.example.com/openapi.yaml", False),
        ("https://docs.example.com/openapi.yml", False),
    ],
)
def test_is_html_url(url: str, expected: bool):
    assert _is_html_url(url) is expected
