"""Tests voor sitemap/link discovery."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.discovery import _is_html_url, _parse_sitemap_xml, discover_pages

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


# ---------------------------------------------------------------------------
# Tests voor discover_pages (async, httpx gemockt)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discover_pages_uses_sitemap_when_available() -> None:
    """discover_pages gebruikt /sitemap.xml als die beschikbaar is."""
    sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://docs.example.com/page1</loc></url>
  <url><loc>https://docs.example.com/page2</loc></url>
</urlset>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/xml"}
    mock_resp.text = sitemap_xml

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.discovery.httpx.AsyncClient", return_value=mock_client):
        result = await discover_pages("https://docs.example.com")

    assert "https://docs.example.com/page1" in result
    assert "https://docs.example.com/page2" in result


@pytest.mark.asyncio
async def test_discover_pages_falls_back_to_crawl_when_no_sitemap() -> None:
    """discover_pages valt terug op link-crawl als er geen sitemap is."""
    page_html = '<html><body><a href="/guide">Guide</a><a href="/api">API</a></body></html>'

    no_sitemap_resp = MagicMock()
    no_sitemap_resp.status_code = 404

    page_resp = MagicMock()
    page_resp.status_code = 200
    page_resp.text = page_html

    call_count = 0

    async def fake_get(url: str, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        # First 3 calls are sitemap candidates → 404
        if call_count <= 3:
            return no_sitemap_resp
        return page_resp

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.discovery.httpx.AsyncClient", return_value=mock_client):
        result = await discover_pages("https://docs.example.com")

    assert isinstance(result, list)
    assert "https://docs.example.com" in result


@pytest.mark.asyncio
async def test_discover_pages_respects_max_pages() -> None:
    """discover_pages geeft maximaal max_pages resultaten terug."""
    urls = "\n".join(
        f"  <url><loc>https://docs.example.com/page{i}</loc></url>"
        for i in range(100)
    )
    sitemap_xml = f"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls}
</urlset>"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/xml"}
    mock_resp.text = sitemap_xml

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.discovery.httpx.AsyncClient", return_value=mock_client):
        result = await discover_pages("https://docs.example.com", max_pages=10)

    assert len(result) <= 10


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
