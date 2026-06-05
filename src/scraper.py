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

from playwright.async_api import Browser, Page, async_playwright

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
        urls:           Te scrapen pagina's.
        login_strategy: Login configuratie.
        headless:       Als None, automatisch bepaald op basis van login mode.
        start_url:      URL voor login; valt terug op urls[0] als None.

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
        if login_strategy.mode != "none" and urls:
            login_url = start_url if start_url is not None else urls[0]
            login_page = await context.new_page()
            await apply_login(login_page, login_strategy, login_url)
            await login_page.close()

        for url in urls:
            try:
                page = await context.new_page()
                scraped = await _scrape_single_page(page, url)
                results.append(scraped)
                await page.close()
            except Exception as e:  # noqa: BLE001
                print(f"⚠️  Fout bij scrapen van {url}: {e}")

        await browser.close()

    return results


async def _scrape_single_page(page: Page, url: str) -> ScrapedPage:
    """Laad één pagina volledig en geef de HTML terug."""
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_load_state("networkidle", timeout=10_000)
    await _scroll_to_bottom(page)
    await _expand_accordions(page)
    await asyncio.sleep(0.5)

    html = await page.content()
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
