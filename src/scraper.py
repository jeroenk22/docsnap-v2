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
from urllib.parse import unquote, urlparse, urlunparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .discovery import _is_html_url
from .login import LoginStrategy, apply_login
from .swagger import SwaggerDetected, detect_swagger_in_page

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

        # Fase 1 — Login
        if login_strategy.mode != "none" and (urls or start_url):
            login_url = start_url if start_url is not None else urls[0]
            login_page = await context.new_page()
            await apply_login(login_page, login_strategy, login_url)
            await login_page.close()

        # Fase 1.5 — Swagger detectie na login
        # Dekt Confluence embedded swagger en reguliere swagger achter login.
        # Gooit SwaggerDetected zodat main.py de spec direct kan exporteren.
        if start_url is not None and login_strategy.mode != "none":
            _sw_page = await context.new_page()
            try:
                await _sw_page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
                _swagger = await detect_swagger_in_page(_sw_page)
                if _swagger:
                    raise SwaggerDetected(_swagger)
            finally:
                await _sw_page.close()

        # Fase 2 — Discovery
        # Voor geauthenticeerde sessies: gebruik de browser (heeft auth-cookies).
        # Voor niet-geauthenticeerde sessies: urls zijn al ontdekt via httpx.
        if start_url is not None and login_strategy.mode != "none":
            print("📡  Pagina's ontdekken via browser...")
            scrape_urls = await _browser_discover_pages(context, start_url)
            print(f"   → {len(scrape_urls)} pagina's gevonden.")
        else:
            scrape_urls = urls

        # Fase 3 — Scrapen
        print("🌐  Pagina's scrapen...")
        total = len(scrape_urls)
        for i, url in enumerate(scrape_urls, 1):
            print(f"   [{i}/{total}] {_fmt_url(url)}")
            try:
                page = await context.new_page()
                scraped = await _scrape_single_page(page, url)
                results.append(scraped)
                await page.close()
            except Exception as e:  # noqa: BLE001
                print(f"⚠️  Fout bij scrapen van {url}: {e}")

        await browser.close()

    return results


def _fmt_url(url: str) -> str:
    """OSC 8 hyperlink — volledige URL als display-tekst voor Ctrl+Click in terminal."""
    return f"\033]8;;{url}\033\\{url}\033]8;;\033\\"


def _norm_url(url: str) -> str:
    """Normaliseer URL voor deduplicatie.

    Strips fragment en querystring, verwijdert trailing slash, lowercase
    scheme en host, en decode percent-encoding in het pad (%20 vs spatie etc.).
    Hierdoor worden URL-varianten van dezelfde pagina als één sleutel herkend.
    """
    p = urlparse(url.split("#")[0].split("?")[0].rstrip("/"))
    return urlunparse((p.scheme.lower(), p.netloc.lower(), unquote(p.path), "", "", ""))


async def _browser_discover_pages(
    context: BrowserContext,
    base_url: str,
    max_pages: int = 500,
) -> list[str]:
    """BFS link discovery via een geauthenticeerde browser context.

    Gebruikt de canonieke URL (page.url na redirect) als sleutel voor
    deduplicatie zodat meerdere URL-vormen van dezelfde pagina — bijv.
    een Confluence ID-URL die doorstuurt naar een titel-URL — als één
    pagina worden geteld.
    """
    parsed = urlparse(base_url)
    base_path = parsed.path.rstrip("/")

    def _in_scope(u: str) -> bool:
        path = urlparse(u).path
        return path == base_path or path.startswith(base_path + "/")

    seen: set[str] = set()     # genormaliseerde URLs die al gezien zijn
    queued: set[str] = set()   # genormaliseerde URLs in de queue (O(1) dedup)
    queue: list[str] = [base_url]
    result: list[str] = []     # canonieke URLs om te scrapen (één per unieke pagina)

    queued.add(_norm_url(base_url))

    page = await context.new_page()
    try:
        while queue and len(result) < max_pages:
            url = queue.pop(0)
            norm = _norm_url(url)
            if norm in seen:
                continue
            seen.add(norm)

            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_load_state("networkidle", timeout=10_000)

                # Sla HTTP-foutpagina's (404, 403, 500 etc.) over — generiek voor
                # elke website. Dode links of verwijderde pagina's worden zo niet
                # in de scrape-lijst opgenomen.
                if response is not None and response.status >= 400:
                    continue

                # Gebruik de canonieke URL na redirect als definitieve URL voor
                # deze pagina. Dit dekt ID-URL→titel-URL, trailing-slash
                # normalisatie, en elke andere server-side redirect — generiek
                # voor alle websites.
                canonical = page.url.split("#")[0].split("?")[0].rstrip("/")
                norm_canonical = _norm_url(canonical)

                if norm_canonical != norm:
                    if norm_canonical in seen:
                        # Pagina-inhoud al verwerkt via een andere URL-vorm
                        continue
                    seen.add(norm_canonical)

                if _in_scope(canonical):
                    result.append(canonical)
                    print(f"   🔍 [{len(result)}/{max_pages}] {_fmt_url(canonical)}")

                links: list[str] = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => e.href)",
                )
                for link in links:
                    clean = link.split("#")[0].split("?")[0].rstrip("/")
                    norm_clean = _norm_url(clean)
                    if (
                        clean
                        and urlparse(clean).netloc.lower() == parsed.netloc.lower()
                        and _in_scope(clean)
                        and norm_clean not in seen
                        and norm_clean not in queued
                        and _is_html_url(clean)
                    ):
                        queue.append(clean)
                        queued.add(norm_clean)
            except Exception:  # noqa: BLE001
                continue
    finally:
        await page.close()

    return sorted(set(result))


async def _scrape_single_page(page: Page, url: str) -> ScrapedPage:
    """Laad één pagina volledig en geef de HTML terug."""
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_load_state("networkidle", timeout=10_000)
    await _scroll_to_bottom(page)
    await _expand_accordions(page)
    await asyncio.sleep(0.5)

    # Probeer semantische content-containers voor een kleinere, schonere HTML.
    # main / article / [role='main'] zijn HTML5-standaard en worden door de
    # meeste documentatiesites (Confluence, ReadTheDocs, GitBook, …) gebruikt.
    # Fallback naar body als geen van de selectors iets substantieels oplevert.
    html = ""
    for selector in ("main", "article", "[role='main']"):
        try:
            candidate = await page.inner_html(selector)
            if len(candidate) > 500:
                html = candidate
                break
        except Exception:  # noqa: BLE001
            pass
    if not html:
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
