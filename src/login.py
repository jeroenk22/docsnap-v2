"""
Login strategieën voor docsnap.

Ondersteunde modi:
- none:   geen login nodig
- form:   automatisch form-login met user/pass
- manual: gebruiker logt zelf in, tool wacht op ENTER
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from playwright.async_api import Page

LoginMode = Literal["none", "form", "manual"]


@dataclass
class LoginStrategy:
    """Configuratie voor een login strategie."""

    mode: LoginMode
    username: str | None = None
    password: str | None = None


def create_login_strategy(
    mode: LoginMode,
    username: str | None = None,
    password: str | None = None,
) -> LoginStrategy:
    """Maak een LoginStrategy op basis van de CLI opties.

    Raises:
        ValueError: Als mode 'form' is maar user/pass ontbreken.
    """
    if mode == "form" and (not username or not password):
        raise ValueError("--user en --pass zijn verplicht bij --login form")
    return LoginStrategy(mode=mode, username=username, password=password)


async def apply_login(page: Page, strategy: LoginStrategy, login_url: str) -> None:
    """Pas de login strategie toe op een Playwright pagina.

    Args:
        page:       De Playwright pagina.
        strategy:   De te gebruiken login strategie.
        login_url:  De URL van de login pagina.
    """
    if strategy.mode == "none":
        return

    await page.goto(login_url, wait_until="networkidle")

    if strategy.mode == "form":
        await _form_login(page, strategy)
    elif strategy.mode == "manual":
        await _manual_login(page)


async def _form_login(page: Page, strategy: LoginStrategy) -> None:
    """Automatisch inloggen via een HTML formulier."""
    username_selectors = [
        'input[type="email"]',
        'input[name="email"]',
        'input[name="username"]',
        'input[name="user"]',
        'input[id*="email"]',
        'input[id*="user"]',
    ]
    password_selectors = ['input[type="password"]']
    submit_selectors = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("Log in")',
        'button:has-text("Sign in")',
    ]

    for sel in username_selectors:
        if await page.locator(sel).count() > 0:
            await page.locator(sel).first.fill(strategy.username or "")
            break

    for sel in password_selectors:
        if await page.locator(sel).count() > 0:
            await page.locator(sel).first.fill(strategy.password or "")
            break

    for sel in submit_selectors:
        if await page.locator(sel).count() > 0:
            await page.locator(sel).first.click()
            break

    await page.wait_for_load_state("networkidle")


async def _manual_login(page: Page) -> None:
    """Wacht totdat de gebruiker handmatig ingelogd is."""
    import click as _click

    _click.echo(
        "\n🔐  Manual login modus — de browser is geopend.\n"
        "   Log in op de documentatiesite en druk daarna op ENTER om door te gaan..."
    )
    input()
    await page.wait_for_load_state("networkidle")
