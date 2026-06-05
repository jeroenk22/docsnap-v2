"""Tests voor sitemap/link discovery."""
from src.discovery import _parse_sitemap_xml

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
