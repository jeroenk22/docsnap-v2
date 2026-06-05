"""
Playwright page loader met slimme wachtstrategie.

Per pagina:
1. Navigeer naar de URL
2. Wacht op networkidle
3. Scroll stap voor stap naar beneden (lazy-loading)
4. Klik alle accordions / collapsible elementen open
5. Geef de volledige HTML terug
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .discovery import _is_html_url
from .login import LoginStrategy, apply_login

# CSS selectors voor veelgebruikte collapsible elementen
ACCORDION_SELECTORS = [
    "details:not([open])",
    "[aria-expanded='false']",
    ".accordion-header:not(.active)",
    ".collapse-toggle",
    "summary",
]


@dataclass
class ScrapedPage:
    """Resultaat van het scrapen van één pagina."""

    url: str
    html: str
    title: str


async def scrape_pages(
    urls: list[str],
    login_strategy: LoginStrategy,
    headless: bool | None = None,
    start_url: str | None = None,
) -> list[ScrapedPage]:
    """Scrape een lijst van URLs en geef de volledige HTML terug.

    Args:
        urls:           Te scrapen pagina's. Mag leeg zijn als start_url opgegeven is
                        en login niet 'none' is — in dat geval worden pagina's via de
                        browser ontdekt na inloggen.
        login_strategy: Login configuratie.
        headless:       Als None, automatisch bepaald op basis van login mode.
        start_url:      URL voor login én voor browser-based discovery.

    Returns:
        Lijst van ScrapedPage objecten.
    """
    # Manual login heeft een zichtbare browser nodig
    if headless is None:
        headless = login_strategy.mode != "manual"

    results: list[ScrapedPage] = []

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        )

        # Login op de startpagina als nodig
        if login_strategy.mode != "none" and (urls or start_url):
            login_url = start_url if start_url is not None else urls[0]
            login_page = await context.new_page()
            await apply_login(login_page, login_strategy, login_url)
            await login_page.close()

        # Voor geauthenticeerde sessies: ontdek pagina's via de browser zodat
        # JS-rendered nav en login-vereiste pagina's ook gevonden worden.
        if start_url is not None and login_strategy.mode != "none":
            print(f"🔍  Browser discovery vanuit {start_url}...")
            scrape_urls = await _browser_discover_pages(context, start_url)
            print(f"   → {len(scrape_urls)} pagina's gevonden.")
        else:
            scrape_urls = urls

        for url in scrape_urls:
            try:
                page = await context.new_page()
                scraped = await _scrape_single_page(page, url)
                results.append(scraped)
                await page.close()
            except Exception as e:  # noqa: BLE001
                print(f"⚠️  Fout bij scrapen van {url}: {e}")

        await browser.close()

    return results


async def _browser_discover_pages(
    context: BrowserContext,
    base_url: str,
    max_pages: int = 500,
) -> list[str]:
    """BFS link discovery via een geauthenticeerde browser context.

    Bezoekt base_url en volgt recursief alle interne HTML-links totdat
    max_pages bereikt is.
    """
    parsed = urlparse(base_url)
    visited: set[str] = set()
    queue: list[str] = [base_url]

    page = await context.new_page()
    try:
        while queue and len(visited) < max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_load_state("networkidle", timeout=10_000)

                links: list[str] = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => e.href)",
                )
                for link in links:
                    clean = link.split("#")[0].split("?")[0].rstrip("/")
                    if (
                        clean
                        and urlparse(clean).netloc == parsed.netloc
                        and clean not in visited
                        and clean not in queue
                        and _is_html_url(clean)
                    ):
                        queue.append(clean)
            except Exception:  # noqa: BLE001
                continue
    finally:
        await page.close()

    return sorted(visited)


async def _scrape_single_page(page: Page, url: str) -> ScrapedPage:
    """Laad één pagina volledig en geef de HTML terug."""
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_load_state("networkidle", timeout=10_000)
    await _scroll_to_bottom(page)
    await _expand_accordions(page)
    await asyncio.sleep(0.5)

    # inner_html('body') slaat de <head> over (CSS/scripts) zodat Claude
    # alleen de zichtbare pagina-inhoud ontvangt.
    html = await page.inner_html("body")
    title = await page.title()
    return ScrapedPage(url=url, html=html, title=title)


async def _scroll_to_bottom(page: Page, step: int = 800, delay: float = 0.2) -> None:
    """Scroll geleidelijk naar de onderkant voor lazy-loading."""
    scroll_height = await page.evaluate("document.body.scrollHeight")
    current_position = 0

    while current_position < scroll_height:
        current_position += step
        await page.evaluate(f"window.scrollTo(0, {current_position})")
        await asyncio.sleep(delay)
        new_height = await page.evaluate("document.body.scrollHeight")
        scroll_height = new_height

    await page.evaluate("window.scrollTo(0, 0)")


async def _expand_accordions(page: Page) -> None:
    """Klik alle collapsible elementen open."""
    for selector in ACCORDION_SELECTORS:
        try:
            elements = page.locator(selector)
            count = await elements.count()
            for i in range(count):
                try:
                    await elements.nth(i).click(timeout=2_000)
                    await asyncio.sleep(0.1)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
