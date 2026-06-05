"""Tests voor de Playwright scraper."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.login import LoginStrategy
from src.scraper import _browser_discover_pages, _scrape_single_page, scrape_pages


def _make_login_strategy(mode: str = "form") -> LoginStrategy:
    return LoginStrategy(mode=mode, username="user", password="pass")


def _make_mock_page(url: str = "https://docs.example.com") -> AsyncMock:
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=0)
    mock_page.locator = MagicMock(return_value=AsyncMock(count=AsyncMock(return_value=0)))
    mock_page.inner_html = AsyncMock(return_value="<div>content</div>")
    mock_page.title = AsyncMock(return_value="Test")
    mock_page.close = AsyncMock()
    mock_page.url = url  # synchrone string-property in echte Playwright
    return mock_page


def _make_mock_playwright(mock_page: AsyncMock) -> tuple[AsyncMock, AsyncMock]:
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

    return mock_pw, mock_context


@pytest.mark.asyncio
async def test_scrape_pages_uses_start_url_for_login() -> None:
    """scrape_pages gebruikt start_url voor login, niet urls[0]."""
    urls = ["https://docs.example.com/image.png", "https://docs.example.com/guide"]
    start_url = "https://docs.example.com"
    login_strategy = _make_login_strategy(mode="form")

    captured_login_urls: list[str] = []

    async def fake_apply_login(page: object, strategy: object, url: str) -> None:
        captured_login_urls.append(url)

    mock_page = _make_mock_page()
    mock_pw, _ = _make_mock_playwright(mock_page)

    with (
        patch("src.scraper.async_playwright", return_value=mock_pw),
        patch("src.scraper.apply_login", side_effect=fake_apply_login),
        # Browser discovery is een apart concern — niet testen hier.
        patch("src.scraper._browser_discover_pages", return_value=urls),
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

    mock_page = _make_mock_page()
    mock_pw, _ = _make_mock_playwright(mock_page)

    with (
        patch("src.scraper.async_playwright", return_value=mock_pw),
        patch("src.scraper.apply_login", side_effect=fake_apply_login),
    ):
        # Geen start_url → geen browser discovery, scrape_urls = urls
        await scrape_pages(urls, login_strategy, headless=True)

    assert captured_login_urls == [urls[0]]


@pytest.mark.asyncio
async def test_scrape_pages_uses_browser_discovery_when_authenticated() -> None:
    """scrape_pages roept _browser_discover_pages aan bij login != none met start_url."""
    start_url = "https://docs.example.com"
    discovered = ["https://docs.example.com/page-a", "https://docs.example.com/page-b"]
    login_strategy = _make_login_strategy(mode="form")

    mock_page = _make_mock_page()
    mock_pw, _ = _make_mock_playwright(mock_page)

    with (
        patch("src.scraper.async_playwright", return_value=mock_pw),
        patch("src.scraper.apply_login"),
        patch("src.scraper._browser_discover_pages", return_value=discovered) as mock_discover,
    ):
        results = await scrape_pages([], login_strategy, headless=True, start_url=start_url)

    mock_discover.assert_called_once()
    assert len(results) == len(discovered)


@pytest.mark.asyncio
async def test_browser_discover_pages_respects_path_prefix() -> None:
    """_browser_discover_pages volgt alleen links binnen het pad van base_url."""
    base_url = "https://support.example.com/space/API"

    # Pagina bevat links naar eigen space, andere space, en extern domein
    all_links = [
        "https://support.example.com/space/API/page-one",   # ✓ binnen prefix
        "https://support.example.com/space/API/page-two",   # ✓ binnen prefix
        "https://support.example.com/space/VIS/other",      # ✗ andere space
        "https://support.example.com/home",                 # ✗ buiten prefix
        "https://other.com/page",                           # ✗ ander domein
    ]

    mock_page = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.close = AsyncMock()
    # page.url is een synchrone property in Playwright; simuleer dat de browser
    # op de genavigeerde URL landt (geen redirect in deze test).
    mock_page.url = base_url

    async def fake_goto(url: str, **kwargs: object) -> None:
        mock_page.url = url  # update url zodat canonical == het genavigeerde adres

    mock_page.goto = AsyncMock(side_effect=fake_goto)
    # Eerste aanroep (base_url zelf) geeft alle links terug; vervolgpagina's geven []
    mock_page.eval_on_selector_all = AsyncMock(side_effect=[all_links, [], []])

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    discovered = await _browser_discover_pages(mock_context, base_url)

    assert "https://support.example.com/space/API/page-one" in discovered
    assert "https://support.example.com/space/API/page-two" in discovered
    assert base_url in discovered  # de startpagina zelf
    assert "https://support.example.com/space/VIS/other" not in discovered
    assert "https://support.example.com/home" not in discovered
    assert "https://other.com/page" not in discovered


@pytest.mark.asyncio
async def test_browser_discover_pages_deduplicates_redirect_variants() -> None:
    """ID-URL en canonical-URL van dezelfde pagina tellen als één pagina."""
    base_url = "https://docs.example.com/space/API"
    id_url = "https://docs.example.com/space/API/12345"
    canonical_url = "https://docs.example.com/space/API/12345/My+Page"

    # Start pagina heeft links naar beide URL-vormen van dezelfde pagina
    start_links = [id_url, canonical_url]

    mock_page = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.close = AsyncMock()
    mock_page.url = base_url

    # Simuleer de Confluence redirect: ID-URL wordt canonical-URL na navigatie
    async def fake_goto(url: str, **kwargs: object) -> None:
        if url == id_url:
            mock_page.url = canonical_url  # redirect ID → canonical
        else:
            mock_page.url = url

    mock_page.goto = AsyncMock(side_effect=fake_goto)
    # Start pagina geeft beide links; canonical en volgende pagina's geven []
    mock_page.eval_on_selector_all = AsyncMock(side_effect=[start_links, [], []])

    mock_context = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    discovered = await _browser_discover_pages(mock_context, base_url)

    # Canonical URL moet aanwezig zijn; ID en canonical mogen NIET allebei aanwezig zijn
    assert canonical_url in discovered
    assert id_url not in discovered  # ID-URL werd canonical — geen duplicaat


@pytest.mark.asyncio
async def test_scrape_single_page_uses_main_selector_when_available() -> None:
    """_scrape_single_page gebruikt <main> als dat substantiële content heeft."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=0)
    mock_page.locator = MagicMock(return_value=AsyncMock(count=AsyncMock(return_value=0)))
    mock_page.title = AsyncMock(return_value="Test Page")

    main_html = "<h1>Docs</h1>" + "x" * 600  # > 500 chars — substantieel
    mock_page.inner_html = AsyncMock(return_value=main_html)

    result = await _scrape_single_page(mock_page, "https://docs.example.com/page")

    # inner_html moet als eerste aangeroepen zijn met 'main'
    assert mock_page.inner_html.call_args_list[0].args[0] == "main"
    assert result.html == main_html


@pytest.mark.asyncio
async def test_scrape_single_page_falls_back_to_body() -> None:
    """_scrape_single_page valt terug op body als main/article te klein zijn."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=0)
    mock_page.locator = MagicMock(return_value=AsyncMock(count=AsyncMock(return_value=0)))
    mock_page.title = AsyncMock(return_value="Test Page")

    body_html = "<div>full body content</div>" + "x" * 600

    # main, article en [role='main'] geven te weinig content terug; body geeft meer
    async def fake_inner_html(selector: str) -> str:
        if selector == "body":
            return body_html
        return "<nav>small</nav>"  # < 500 chars

    mock_page.inner_html = AsyncMock(side_effect=fake_inner_html)

    result = await _scrape_single_page(mock_page, "https://docs.example.com/page")

    assert result.html == body_html
