"""
Ontdek alle documentatiepagina's via sitemap.xml, nav-links of recursieve crawl.

Strategie (in volgorde van voorkeur):
1. Probeer /sitemap.xml te laden
2. Probeer /sitemap_index.xml
3. Crawl recursief interne links als fallback
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx

_NON_HTML_EXTENSIONS = frozenset(
    [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".css", ".js", ".mjs", ".ts", ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".zip", ".tar", ".gz", ".xml", ".json", ".yaml", ".yml"]
)


def _is_html_url(url: str) -> bool:
    """Return True als de URL waarschijnlijk een HTML-pagina is."""
    path = urlparse(url).path.lower()
    _, _, ext = path.rpartition(".")
    return not ext or f".{ext}" not in _NON_HTML_EXTENSIONS


async def discover_pages(base_url: str, max_pages: int = 500) -> list[str]:
    """Ontdek alle pagina's op de documentatiesite.

    Args:
        base_url:  Startpagina URL.
        max_pages: Maximaal aantal te scrapen pagina's.

    Returns:
        Gesorteerde lijst van unieke pagina-URLs.
    """
    sitemap_urls = await _try_sitemap(base_url)
    if sitemap_urls:
        return sitemap_urls[:max_pages]
    return await _crawl_nav_links(base_url, max_pages)


async def _try_sitemap(base_url: str) -> list[str]:
    """Probeer sitemap.xml of sitemap_index.xml te laden."""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    candidates = [
        f"{origin}/sitemap.xml",
        f"{origin}/sitemap_index.xml",
        f"{origin}/docs/sitemap.xml",
    ]

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for sitemap_url in candidates:
            try:
                resp = await client.get(sitemap_url)
                if resp.status_code == 200 and "xml" in resp.headers.get("content-type", ""):
                    return _parse_sitemap_xml(resp.text, base_url)
            except httpx.RequestError:
                continue
    return []


def _parse_sitemap_xml(xml_content: str, base_url: str) -> list[str]:
    """Extraheer URLs uit sitemap XML die binnen het pad van base_url vallen.

    Args:
        xml_content: De raw XML inhoud.
        base_url:    De basis URL (voor domein- en padprefix-filtering).

    Returns:
        Lijst van URLs op hetzelfde domein én binnen het pad van base_url.
    """
    parsed = urlparse(base_url)
    base_path = parsed.path.rstrip("/")
    urls = re.findall(r"<loc>(https?://[^<]+)</loc>", xml_content)
    return [
        u for u in urls
        if urlparse(u).netloc == parsed.netloc
        and _is_html_url(u)
        and (
            not base_path
            or urlparse(u).path == base_path
            or urlparse(u).path.startswith(base_path + "/")
        )
    ]


async def _crawl_nav_links(base_url: str, max_pages: int) -> list[str]:
    """Crawl recursief interne links als fallback voor ontbrekende sitemap."""
    visited: set[str] = set()
    queue: list[str] = [base_url]
    parsed_base = urlparse(base_url)
    origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
    base_path = parsed_base.path.rstrip("/")

    def _in_scope(url: str) -> bool:
        path = urlparse(url).path
        return not base_path or path == base_path or path.startswith(base_path + "/")

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        while queue and len(visited) < max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                links = re.findall(r'href="(/[^"#?]*)"', resp.text)
                for link in links:
                    full_url = urljoin(origin, link)
                    if (
                        full_url not in visited
                        and full_url not in queue
                        and _is_html_url(full_url)
                        and _in_scope(full_url)
                    ):
                        queue.append(full_url)
            except httpx.RequestError:
                continue

    return sorted(visited)
