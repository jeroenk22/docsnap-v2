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
import contextlib
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse, urlunparse

from playwright.async_api import BrowserContext, Page, async_playwright

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
    extra_wait: float = 0.0,
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

    # Probe: controleer of de startpagina bot-bescherming heeft.
    # Als ja, schakel over naar headed mode zodat de gebruiker de challenge kan oplossen.
    probe_url = start_url or (urls[0] if urls else None)
    bot_challenge_detected = (
        headless and bool(probe_url) and await _httpx_has_bot_challenge(probe_url)
    )
    if bot_challenge_detected:
        print("🤖  Bot-bescherming gedetecteerd (Cloudflare/CAPTCHA).")
        print("   Browser opent zichtbaar — los de challenge op in de browser.")
        headless = False

    results: list[ScrapedPage] = []

    async with async_playwright() as pw:
        # Bij een bot-challenge: gebruik de echte Chrome-browser van de gebruiker
        # (channel="chrome"). Chrome heeft een echte fingerprint (GPU, fonts,
        # extensies) die bot-detectie zoals Cloudflare vertrouwt. Chromium mist dit.
        # Voor headless sessies zonder challenge volstaat Chromium.
        if bot_challenge_detected:
            try:
                browser = await pw.chromium.launch(
                    headless=False,
                    channel="chrome",
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception:  # noqa: BLE001
                # Chrome niet geïnstalleerd — val terug op Chromium headed
                browser = await pw.chromium.launch(
                    headless=False,
                    args=["--disable-blink-features=AutomationControlled"],
                )
        else:
            browser = await pw.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
        context = await browser.new_context()
        # Verberg automation-markeringen die Cloudflare en andere bot-detectie
        # triggeren. navigator.webdriver is altijd 'true' in een standaard
        # Playwright-browser; dit script overschrijft dat vóór elke paginalading.
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        # Fase 0.5 — Wacht op challenge-oplossing als bot-bescherming gedetecteerd
        if bot_challenge_detected and probe_url:
            _ch_page = await context.new_page()
            try:
                await _ch_page.goto(
                    probe_url, wait_until="domcontentloaded", timeout=30_000
                )
                with contextlib.suppress(Exception):
                    await _ch_page.wait_for_load_state("networkidle", timeout=5_000)
                if _is_bot_challenge(await _ch_page.content()):
                    print("   Wachten tot challenge opgelost is...")
                    await _wait_for_challenge_solved(_ch_page)
                print("   ✅  Challenge opgelost — verder met scrapen.")
            finally:
                await _ch_page.close()

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
                await _sw_page.goto(
                    start_url, wait_until="domcontentloaded", timeout=30_000
                )
                _swagger = await detect_swagger_in_page(_sw_page)
                if _swagger:
                    raise SwaggerDetected(_swagger)
            finally:
                await _sw_page.close()

        # Fase 2 — Discovery
        # Voor geauthenticeerde sessies én voor JS-zware sites waarbij httpx geen
        # links vond (urls leeg): gebruik de browser voor discovery.
        if start_url is not None and (login_strategy.mode != "none" or not urls):
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
                scraped = await _scrape_single_page(page, url, extra_wait=extra_wait)
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


async def _wait_for_js_content(
    page: Page, stable_for: float = 1.5, timeout: float = 12.0
) -> None:
    """Wacht tot het aantal links op de pagina stabiel is.

    Confluence en andere SPA's laden de zijbalk-navigatie asynchroon ná
    networkidle. We pollen het aantal <a>-tags totdat dat aantal minstens
    `stable_for` seconden niet meer veranderd is — dan weten we dat de
    navigatie volledig geladen is.
    """
    import time

    deadline = time.monotonic() + timeout
    prev_count = -1
    stable_since = time.monotonic()

    while time.monotonic() < deadline:
        count: int = await page.eval_on_selector_all("a[href]", "els => els.length")
        now = time.monotonic()
        if count != prev_count:
            prev_count = count
            stable_since = now
        elif now - stable_since >= stable_for:
            return  # link-count stabiel voor stable_for seconden
        await asyncio.sleep(0.3)


async def _claude_identify_nav_links(
    same_domain_links: list[str], base_url: str
) -> list[str]:
    """Vraag Claude welke same-domain links documentatie-navigatielinks zijn.

    Wordt aangeroepen als de pad-gebaseerde scope geen child-pagina's oplevert.
    Claude herkent de werkelijke URL-structuur van de site (bijv. Confluence
    /display/SPACE/ i.p.v. /space/SPACE/) en geeft de relevante links terug.
    """
    import json
    import os

    import anthropic

    if not same_domain_links:
        return []

    client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    unique = list(dict.fromkeys(same_domain_links))[:150]

    prompt = (
        f"Start URL: {base_url}\n\n"
        "Same-domain links found on this documentation page:\n"
        + "\n".join(unique)
        + "\n\nWhich of these are links to documentation content pages in the same section "
        "as the start URL? Exclude: login, admin, user profile, search, images, CSS/JS files.\n"
        "Return ONLY a JSON array of the relevant URLs, nothing else."
    )

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Strip markdown code fences if model wraps in ```json ... ```
        text = re.sub(r"^```[a-z]*\n?", "", text).rstrip("`").strip()
        result = json.loads(text)
        return [u for u in result if isinstance(u, str)]
    except Exception:  # noqa: BLE001
        return []


def _common_path_prefix(urls: list[str]) -> str:
    """Geeft het diepste gemeenschappelijke pad-prefix van een lijst URLs."""
    paths = [urlparse(u).path for u in urls if u]
    split_paths = [p.strip("/").split("/") for p in paths if p.strip("/")]
    if not split_paths:
        return ""
    common: list[str] = []
    for parts in zip(*split_paths, strict=False):
        if len({p.lower() for p in parts}) == 1:
            common.append(parts[0])
        else:
            break
    return ("/" + "/".join(common)) if common else ""


async def _browser_discover_pages(
    context: BrowserContext,
    base_url: str,
    max_pages: int = 500,
) -> list[str]:
    """BFS link discovery via een geauthenticeerde browser context.

    Stap 1 — Wacht op JS-rendering: na networkidle pollen we kort tot de pagina
    genoeg links heeft (SPA's bouwen DOM soms na networkidle verder op).

    Stap 2 — Pad-prefix scope: links worden gefilterd op het pad van de start-URL.

    Stap 3 — Claude-fallback: als de eerste pagina wél same-domain links heeft
    maar geen ervan valt in scope (bijv. Confluence /display/SPACE/ i.p.v.
    /space/SPACE/), vraagt Claude welke links documentatie-navigatielinks zijn.
    Op basis van het antwoord wordt de scope automatisch bijgesteld.

    Cloudflare-detectie en -afhandeling lopen vóór deze functie (in scrape_pages)
    en worden hier niet geraakt.
    """
    parsed = urlparse(base_url)
    base_path = parsed.path.rstrip("/")

    # Mutable scope zodat de Claude-fallback het pad kan bijstellen tijdens de BFS.
    scope: dict[str, str] = {"path": base_path}

    def _in_scope(u: str) -> bool:
        path = urlparse(u).path
        bp = scope["path"]
        return not bp or path == bp or path.startswith(bp + "/")

    def _enqueue(link: str) -> None:
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

    seen: set[str] = set()
    queued: set[str] = set()
    queue: list[str] = [base_url]
    result: list[str] = []
    first_page = True

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
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=30_000
                )
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=10_000)

                # Extra wachttijd voor SPA's die na networkidle nog links renderen.
                await _wait_for_js_content(page)

                # Wacht expliciet op client-side redirect: als de browser nog op de
                # start-URL staat na alle waits, kan er een JS-redirect onderweg zijn.
                if page.url.rstrip("/") == url.rstrip("/"):
                    try:
                        await page.wait_for_url(
                            lambda u, _url=url: u.rstrip("/") != _url.rstrip("/"),
                            timeout=5_000,
                        )
                        await _wait_for_js_content(
                            page
                        )  # wacht ook op de doorgestuurde pagina
                    except Exception:  # noqa: BLE001
                        pass  # geen redirect binnen 5s — dan is dit de eindpagina

                if response is not None and response.status >= 400:
                    continue

                canonical = page.url.split("#")[0].split("?")[0].rstrip("/")
                norm_canonical = _norm_url(canonical)

                if norm_canonical != norm:
                    if norm_canonical in seen:
                        continue
                    seen.add(norm_canonical)

                if _in_scope(canonical):
                    result.append(canonical)
                    print(f"   🔍 [{len(result)}/{max_pages}] {_fmt_url(canonical)}")

                links: list[str] = await page.eval_on_selector_all(
                    "a[href]",
                    "els => els.map(e => e.href)",
                )
                same_domain = [
                    link
                    for link in links
                    if urlparse(link).netloc.lower() == parsed.netloc.lower()
                ]

                # Na de eerste pagina: als er same-domain links zijn maar geen
                # ervan valt in scope, vraagt Claude welke links relevant zijn en
                # wordt het scope-pad automatisch bijgesteld.
                if first_page:
                    first_page = False
                    in_scope_children = [
                        link
                        for link in same_domain
                        if _in_scope(link.split("#")[0].split("?")[0].rstrip("/"))
                        and _norm_url(link.split("#")[0].split("?")[0].rstrip("/"))
                        not in seen
                    ]
                    if not in_scope_children and same_domain:
                        print(
                            "   🤖  Pad-prefix niet herkend — Claude analyseert navigatiestructuur..."
                        )
                        claude_links = await _claude_identify_nav_links(
                            same_domain, base_url
                        )
                        if claude_links:
                            new_prefix = _common_path_prefix(claude_links)
                            if new_prefix and new_prefix != scope["path"]:
                                print(f"   → Scope bijgewerkt naar: {new_prefix}")
                                scope["path"] = new_prefix
                            for link in claude_links:
                                _enqueue(link)
                        elif not same_domain:
                            print(
                                "   ⚠️  Geen links gevonden — probeer --login manual als de site inloggen vereist."
                            )

                for link in same_domain:
                    _enqueue(link)

            except Exception:  # noqa: BLE001
                if first_page:
                    first_page = False
                continue
    finally:
        await page.close()

    return sorted(set(result))


async def _scrape_single_page(
    page: Page, url: str, extra_wait: float = 0.0
) -> ScrapedPage:
    """Laad één pagina volledig en geef de HTML terug."""
    # networkidle wacht op Cloudflare-redirect-chains; timeout is niet-fataal
    # zodat sites met continue achtergrond-requests ook werken.
    with contextlib.suppress(Exception):
        await page.goto(url, wait_until="networkidle", timeout=30_000)

    # Detecteer en wacht op per-pagina bot-challenge (bijv. Cloudflare op subpagina's)
    try:
        if _is_bot_challenge(await page.content()):
            await _wait_for_challenge_solved(page, timeout=60)
            await asyncio.sleep(1)
    except Exception:  # noqa: BLE001
        pass

    if extra_wait:
        await asyncio.sleep(extra_wait)

    await _scroll_to_bottom(page)
    await _expand_accordions(page)
    await asyncio.sleep(0.5)

    # Probeer semantische content-containers voor een kleinere, schonere HTML.
    # main / article / [role='main'] zijn HTML5-standaard en worden door de
    # meeste documentatiesites (Confluence, ReadTheDocs, GitBook, …) gebruikt.
    # Fallback naar body als geen van de selectors iets substantieels oplevert.
    # query_selector eerst: inner_html wacht anders 30s op een ontbrekend element.
    html = ""
    for selector in ("main", "article", "[role='main']"):
        try:
            if await page.query_selector(selector) is None:
                continue
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


def _is_bot_challenge(html: str) -> bool:
    """Detecteer generieke bot-bescherming (Cloudflare, hCaptcha, reCAPTCHA, etc.)."""
    indicators = [
        "cf-browser-verification",
        "cf_chl_opt",
        "just a moment",
        "enable javascript and cookies to continue",
        "checking if the site connection is secure",
        "hcaptcha.com/1/api.js",
        "recaptcha/api.js",
        "verify you are human",
        "verify you're human",
        "i am not a robot",
    ]
    lower = html.lower()
    return any(ind in lower for ind in indicators)


async def _httpx_has_bot_challenge(url: str) -> bool:
    """Snel via httpx controleren of de pagina bot-bescherming heeft.

    httpx heeft geen JS, waardoor Cloudflare-pagina's altijd de challenge
    teruggeven — ideaal als snelle probe zonder Playwright te starten.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(url)
            return _is_bot_challenge(resp.text)
    except Exception:  # noqa: BLE001
        return False


async def _wait_for_challenge_solved(page: Page, timeout: int = 120) -> None:
    """Wacht (polling) tot de bot-challenge-pagina verdwenen is."""
    for _ in range(timeout):
        await asyncio.sleep(1)
        try:
            if not _is_bot_challenge(await page.content()):
                return
        except Exception:  # noqa: BLE001
            return


async def _expand_accordions(page: Page) -> None:
    """Zet alle collapsible elementen open via JavaScript (betrouwbaarder dan klikken).

    JavaScript-aanpak werkt ook als elementen buiten de viewport vallen of als
    click-handlers de focus stelen. Click-aanpak als fallback voor widgets die
    via JS niet reageren (bijv. custom React/Vue componenten met eigen state).
    """
    # Stap 1: forceer via JavaScript — dekt <details>, aria-expanded, hidden panels
    try:
        await page.evaluate("""() => {
            // Open alle <details> elementen
            document.querySelectorAll('details:not([open])').forEach(el => { el.open = true; });
            // Zet aria-expanded op true
            document.querySelectorAll('[aria-expanded="false"]').forEach(el => {
                el.setAttribute('aria-expanded', 'true');
            });
            // Verwijder hidden/collapsed klassen die content verbergen
            document.querySelectorAll('[class*="collapsed"],[class*="is-closed"],[class*="closed"]').forEach(el => {
                el.classList.remove('collapsed', 'is-closed', 'closed');
            });
        }""")
        await asyncio.sleep(0.3)
    except Exception:  # noqa: BLE001
        pass

    # Stap 2: klik alsnog op widgets die eigen React/Vue state bijhouden
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
