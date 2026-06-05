"""Tests voor de Playwright scraper."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.login import LoginStrategy
from src.scraper import scrape_pages


def _make_login_strategy(mode: str = "form") -> LoginStrategy:
    return LoginStrategy(mode=mode, username="user", password="pass")


@pytest.mark.asyncio
async def test_scrape_pages_uses_start_url_for_login() -> None:
    """scrape_pages gebruikt start_url voor login, niet urls[0]."""
    urls = ["https://docs.example.com/image.png", "https://docs.example.com/guide"]
    start_url = "https://docs.example.com"
    login_strategy = _make_login_strategy(mode="form")

    captured_login_urls: list[str] = []

    async def fake_apply_login(page: object, strategy: object, url: str) -> None:
        captured_login_urls.append(url)

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=0)
    mock_page.locator = MagicMock(return_value=AsyncMock(count=AsyncMock(return_value=0)))
    mock_page.content = AsyncMock(return_value="<html></html>")
    mock_page.title = AsyncMock(return_value="Test")
    mock_page.close = AsyncMock()

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_chromium = AsyncMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = AsyncMock()
    mock_pw.chromium = mock_chromium
    mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.scraper.async_playwright", return_value=mock_pw),
        patch("src.scraper.apply_login", side_effect=fake_apply_login),
    ):
        await scrape_pages(urls, login_strategy, headless=True, start_url=start_url)

    assert captured_login_urls == [start_url]
    assert urls[0] not in captured_login_urls


@pytest.mark.asyncio
async def test_scrape_pages_falls_back_to_urls0_when_no_start_url() -> None:
    """Als start_url niet gegeven is, gebruikt scrape_pages urls[0] als login URL."""
    urls = ["https://docs.example.com/page-a", "https://docs.example.com/page-b"]
    login_strategy = _make_login_strategy(mode="form")

    captured_login_urls: list[str] = []

    async def fake_apply_login(page: object, strategy: object, url: str) -> None:
        captured_login_urls.append(url)

    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=0)
    mock_page.locator = MagicMock(return_value=AsyncMock(count=AsyncMock(return_value=0)))
    mock_page.content = AsyncMock(return_value="<html></html>")
    mock_page.title = AsyncMock(return_value="Test")
    mock_page.close = AsyncMock()

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_chromium = AsyncMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_pw = AsyncMock()
    mock_pw.chromium = mock_chromium
    mock_pw.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_pw.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.scraper.async_playwright", return_value=mock_pw),
        patch("src.scraper.apply_login", side_effect=fake_apply_login),
    ):
        await scrape_pages(urls, login_strategy, headless=True)

    assert captured_login_urls == [urls[0]]
