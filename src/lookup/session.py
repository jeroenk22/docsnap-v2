"""Browsersessie per bron: hergebruiken, login-muur herkennen, inloggen.

De sessie (cookies, localStorage, IndexedDB) wordt als Playwright storage_state
bewaard in ~/.doc-lookup/sites/<bron>/auth/state.json en hergebruikt over chats
en projecten heen, tot de site hem laat verlopen. Claude ziet nooit een
wachtwoord: de gebruiker logt zelf in in een zichtbaar venster, of de gegevens
komen uit ~/.doc-lookup/.env.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page, Response, async_playwright

from ..scraper import _is_bot_challenge
from .render import content_selectors, find_root, goto, wait_until_stable
from .site import Site, private_dir

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

LOGIN_URL_RE = re.compile(
    r"(log-?in|sign-?in|signon|sso|saml|oauth|openid|/auth\b|/account/)", re.I
)

# Sites die geen redirect doen maar een melding tonen ("Inloggen vereist")
LOGIN_TEXT_RE = re.compile(
    r"(inloggen (is )?vereist|login (is )?vereist|aanmelden (is )?vereist"
    r"|alleen (beschikbaar|zichtbaar) voor ingelogde"
    r"|(je|u) moet (eerst )?(ingelogd|aangemeld) zijn"
    r"|log (eerst )?in om|meld (je|u) (eerst )?aan om"
    r"|login required|sign[- ]in required|please (log|sign) in"
    r"|you (must|need to) (be logged in|log in|sign in)"
    r"|(log|sign) in to (continue|view|access))",
    re.I,
)

# Een zichtbaar wachtwoordveld mét een veld voor gebruikersnaam/e-mail ernaast. Alleen een
# wachtwoordveld is vaak het tokenveld van een API-console ("Try it"), geen login.
LOGIN_FORM_JS = r"""
() => {
  const visible = e => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  return [...document.querySelectorAll('input[type=password]')].filter(visible).some(pw => {
    let box = pw.closest('form') || pw.parentElement;
    for (let i = 0; i < 4 && box && !box.querySelector('input[type=email], input[type=text], input:not([type])'); i++)
      box = box.parentElement;
    return !!box && [...box.querySelectorAll('input[type=email], input[type=text], input:not([type])')]
      .some(u => u !== pw && visible(u));
  });
}
"""

USER_FIELDS = [
    'input[type="email"]',
    'input[name="email"]',
    'input[name="username"]',
    'input[name="os_username"]',
    'input[name*="user" i]',
    'input[id*="user" i]',
    'input[name*="login" i]',
    'input[type="text"]',
    "input:not([type])",
]
SUBMIT_BUTTONS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Log in")',
    'button:has-text("Inloggen")',
    'button:has-text("Sign in")',
]


class BrowserUnavailable(Exception):
    """Chromium start niet (niet geïnstalleerd, of geen beeldscherm voor headed)."""


@asynccontextmanager
async def open_context(
    site: Site, headless: bool = True, use_auth: bool = True
) -> AsyncIterator[BrowserContext]:
    """Browsercontext met de opgeslagen sessie; ververst die sessie na afloop."""
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=headless)
        except Exception as e:  # noqa: BLE001
            raise BrowserUnavailable(str(e).splitlines()[0][:200]) from e
        kwargs: dict = {
            "user_agent": UA,
            "viewport": {"width": 1366, "height": 900},
            "locale": "nl-NL",
        }
        with_auth = use_auth and site.has_auth()
        if with_auth:
            kwargs["storage_state"] = str(site.auth_path)
        context = await browser.new_context(**kwargs)
        context.set_default_timeout(30_000)
        try:
            yield context
        finally:
            if with_auth:
                await save_session(context, site)  # roterende tokens bijwerken
            await context.close()
            await browser.close()


async def save_session(context: BrowserContext, site: Site) -> None:
    """Sla de sessie op, alleen leesbaar voor de eigenaar."""
    private_dir(site.auth_path.parent)
    await context.storage_state(path=str(site.auth_path), indexed_db=True)
    os.chmod(site.auth_path, 0o600)


async def is_auth_wall(
    page: Page, response: Response | None, site: Site, root_found: bool
) -> str | None:
    """Geeft de reden terug als we op een login- of controlepagina zitten, anders None."""
    if response is not None and response.status in (401, 403):
        return f"HTTP {response.status}"
    try:
        if await page.evaluate(LOGIN_FORM_JS):
            return "inlogformulier zichtbaar"
    except Exception:  # noqa: BLE001  (pagina navigeert net)
        pass
    if root_found:
        return None

    url = urlparse(page.url)
    login_url = site.get("login.login_url")
    if login_url:
        lu = urlparse(login_url)
        if url.netloc == lu.netloc and url.path.rstrip("/") == lu.path.rstrip("/"):
            return "doorgestuurd naar loginpagina"
    if LOGIN_URL_RE.search(url.path) or (
        LOGIN_URL_RE.search(url.netloc) and url.netloc != urlparse(site.base_url).netloc
    ):
        return f"doorgestuurd naar {url.netloc}{url.path}"
    marker = site.get("login.logged_in_selector")
    if marker and await page.locator(marker).count() == 0:
        return "ingelogd-kenmerk ontbreekt"
    try:
        text = await page.evaluate(
            "() => (document.body?.innerText || '').slice(0, 5000)"
        )
    except Exception:  # noqa: BLE001
        text = ""
    if m := LOGIN_TEXT_RE.search(text):
        return f"melding '{m.group(0)}'"
    try:
        if _is_bot_challenge(await page.content()):
            return "bot-controle (zoals Cloudflare) die een zichtbare browser vraagt"
    except Exception:  # noqa: BLE001
        pass
    return None


async def _logged_in_now(page: Page, site: Site) -> bool:
    """Staat de browser op de documentatiesite zonder login-muur?"""
    try:
        _, info = await find_root(page, content_selectors(site))
        rooted = bool(info and info["chars"] > 150)
        wall = await is_auth_wall(page, None, site, rooted)
    except Exception:  # noqa: BLE001  (pagina navigeert net)
        return False
    on_site = urlparse(page.url).netloc == urlparse(site.base_url).netloc
    return wall is None and on_site


async def login_interactive(site: Site, timeout: int = 300) -> str | None:
    """Open een zichtbaar venster en wacht tot de gebruiker zelf is ingelogd.

    Geen ENTER nodig (Claude Code heeft geen stdin): het script ziet zelf
    wanneer de login-muur weg is. Returns None bij succes, anders de reden.
    """
    async with open_context(site, headless=False) as context:
        page = await context.new_page()
        await goto(page, site.base_url)
        deadline = time.monotonic() + timeout
        ok_since: float | None = None
        while time.monotonic() < deadline:
            await asyncio.sleep(1)
            if await _logged_in_now(page, site):
                ok_since = ok_since or time.monotonic()
                if time.monotonic() - ok_since > 2.5:  # stabiel ingelogd
                    break
            else:
                ok_since = None
        else:
            return "geen geslaagde login gezien binnen de tijd"
        return await _verify_and_save(context, page, site)


async def login_with_form(site: Site, user: str, password: str) -> str | None:
    """Headless inloggen met gegevens uit ~/.doc-lookup/.env (alleen zonder MFA)."""
    async with open_context(site, use_auth=False) as context:
        page = await context.new_page()
        await goto(page, site.base_url)
        for sel in USER_FIELDS:
            if await page.locator(sel).count():
                await page.locator(sel).first.fill(user)
                break
        await page.locator('input[type="password"]').first.fill(password)
        for sel in SUBMIT_BUTTONS:
            if await page.locator(sel).count():
                await page.locator(sel).first.click()
                break
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=15_000)
        return await _verify_and_save(context, page, site)


async def _verify_and_save(
    context: BrowserContext, page: Page, site: Site
) -> str | None:
    """Controleer op de startpagina dat we binnen zijn en bewaar dan de sessie."""
    resp = await goto(page, site.base_url)
    _, sel, chars = await wait_until_stable(page, site, timeout=15)
    min_chars = int(site.get("content.min_chars", 150))
    wall = await is_auth_wall(page, resp, site, bool(sel and chars >= min_chars))
    if wall is None:
        await save_session(context, site)
    return wall
