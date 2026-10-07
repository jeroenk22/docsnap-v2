"""Tests voor de Playwright scraper."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.login import LoginStrategy
from src.scraper import (
    _browser_discover_pages,
    _httpx_has_bot_challenge,
    _is_bot_challenge,
    _scrape_single_page,
    _wait_for_challenge_solved,
    scrape_pages,
)


def _make_login_strategy(mode: str = "form") -> LoginStrategy:
    return LoginStrategy(mode=mode, username="user", password="pass")


def _make_mock_page(url: str = "https://docs.example.com") -> AsyncMock:
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=0)
    mock_page.locator = MagicMock(
        return_value=AsyncMock(count=AsyncMock(return_value=0))
    )
    mock_page.inner_html = AsyncMock(return_value="<div>content</div>")
    mock_page.content = AsyncMock(return_value="<html><body></body></html>")
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
        patch("src.scraper._httpx_has_bot_challenge", return_value=False),
    ):
        # Geen start_url → geen browser discovery, scrape_urls = urls
        # headless=None zodat regel 63 (automatische bepaling) geraakt wordt
        await scrape_pages(urls, login_strategy, headless=None)

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
        patch(
            "src.scraper._browser_discover_pages", return_value=discovered
        ) as mock_discover,
    ):
        results = await scrape_pages(
            [], login_strategy, headless=True, start_url=start_url
        )

    mock_discover.assert_called_once()
    assert len(results) == len(discovered)


@pytest.mark.asyncio
async def test_browser_discover_pages_respects_path_prefix() -> None:
    """_browser_discover_pages volgt alleen links binnen het pad van base_url."""
    base_url = "https://support.example.com/space/API"

    # Pagina bevat links naar eigen space, andere space, en extern domein
    all_links = [
        "https://support.example.com/space/API/page-one",  # ✓ binnen prefix
        "https://support.example.com/space/API/page-two",  # ✓ binnen prefix
        "https://support.example.com/space/VIS/other",  # ✗ andere space
        "https://support.example.com/home",  # ✗ buiten prefix
        "https://other.com/page",  # ✗ ander domein
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

    # _wait_for_js_content pollt eval_on_selector_all tot de link-count stabiel is;
    # patchen zodat het de gemockte link-lijsten niet opgebruikt.
    with patch("src.scraper._wait_for_js_content", AsyncMock()):
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

    # _wait_for_js_content pollt eval_on_selector_all tot de link-count stabiel is;
    # patchen zodat het de gemockte link-lijsten niet opgebruikt.
    with patch("src.scraper._wait_for_js_content", AsyncMock()):
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
    mock_page.locator = MagicMock(
        return_value=AsyncMock(count=AsyncMock(return_value=0))
    )
    mock_page.title = AsyncMock(return_value="Test Page")
    mock_page.content = AsyncMock(return_value="<html><body></body></html>")

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
    mock_page.locator = MagicMock(
        return_value=AsyncMock(count=AsyncMock(return_value=0))
    )
    mock_page.title = AsyncMock(return_value="Test Page")
    mock_page.content = AsyncMock(return_value="<html><body></body></html>")

    body_html = "<div>full body content</div>" + "x" * 600

    # main, article en [role='main'] geven te weinig content terug; body geeft meer
    async def fake_inner_html(selector: str) -> str:
        if selector == "body":
            return body_html
        return "<nav>small</nav>"  # < 500 chars

    mock_page.inner_html = AsyncMock(side_effect=fake_inner_html)

    result = await _scrape_single_page(mock_page, "https://docs.example.com/page")

    assert result.html == body_html


@pytest.mark.asyncio
async def test_scrape_single_page_waits_for_bot_challenge() -> None:
    """_scrape_single_page wacht op bot-challenge als de pagina er één toont."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value=0)
    mock_page.locator = MagicMock(
        return_value=AsyncMock(count=AsyncMock(return_value=0))
    )
    mock_page.title = AsyncMock(return_value="Test Page")
    mock_page.inner_html = AsyncMock(return_value="<h1>Docs</h1>" + "x" * 600)
    # Eerste aanroep → challenge-pagina; tweede aanroep (in _wait_for_challenge_solved) → gewoon
    mock_page.content = AsyncMock(
        side_effect=[
            "Just a moment... Enable JavaScript and cookies to continue",
            "<html><body><h1>Docs</h1></body></html>",
        ]
    )

    with patch(
        "src.scraper._wait_for_challenge_solved", new_callable=AsyncMock
    ) as mock_wait:
        await _scrape_single_page(mock_page, "https://docs.example.com/page")

    mock_wait.assert_called_once()


@pytest.mark.asyncio
async def test_scrape_single_page_handles_content_exception() -> None:
    """_scrape_single_page crasht niet als page.content() een exception gooit."""
    mock_page = AsyncMock()
    mock_page.goto = AsyncMock()
    mock_page.content = AsyncMock(side_effect=Exception("page closed"))
    mock_page.evaluate = AsyncMock(return_value=0)
    mock_page.locator = MagicMock(
        return_value=AsyncMock(count=AsyncMock(return_value=0))
    )
    mock_page.title = AsyncMock(return_value="Test")
    mock_page.inner_html = AsyncMock(return_value="<h1>Docs</h1>" + "x" * 600)

    result = await _scrape_single_page(mock_page, "https://docs.example.com/page")
    assert result is not None


# ---------------------------------------------------------------------------
# Tests voor bot-challenge detectie
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_pages_logs_error_when_scraping_fails() -> None:
    """scrape_pages logt een waarschuwing als een pagina niet gescraped kan worden."""
    urls = ["https://docs.example.com/page"]
    login_strategy = _make_login_strategy(mode="none")

    mock_page = _make_mock_page()
    mock_pw, _ = _make_mock_playwright(mock_page)

    with (
        patch("src.scraper.async_playwright", return_value=mock_pw),
        patch("src.scraper._httpx_has_bot_challenge", return_value=False),
        patch(
            "src.scraper._scrape_single_page",
            side_effect=Exception("connection refused"),
        ),
    ):
        results = await scrape_pages(urls, login_strategy, headless=True)

    assert results == []


def test_is_bot_challenge_cloudflare():
    """Cloudflare challenge-pagina wordt herkend."""
    html = "<title>Just a moment...</title><p>Enable JavaScript and cookies to continue</p>"
    assert _is_bot_challenge(html)


def test_is_bot_challenge_hcaptcha():
    """hCaptcha-pagina wordt herkend."""
    html = '<script src="https://hcaptcha.com/1/api.js"></script>'
    assert _is_bot_challenge(html)


def test_is_bot_challenge_recaptcha():
    """reCAPTCHA-pagina wordt herkend."""
    html = '<script src="https://www.google.com/recaptcha/api.js"></script>'
    assert _is_bot_challenge(html)


def test_is_bot_challenge_normal_page():
    """Gewone documentatiepagina wordt NIET herkend als challenge."""
    html = "<html><body><h1>API Reference</h1><p>Docs here.</p></body></html>"
    assert not _is_bot_challenge(html)


@pytest.mark.asyncio
async def test_httpx_has_bot_challenge_detects_challenge() -> None:
    """_httpx_has_bot_challenge retourneert True voor een Cloudflare-pagina."""
    mock_resp = MagicMock()
    mock_resp.text = "Just a moment... Enable JavaScript and cookies to continue"

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await _httpx_has_bot_challenge("https://example.com")

    assert result is True


@pytest.mark.asyncio
async def test_httpx_has_bot_challenge_returns_false_for_normal_page() -> None:
    """_httpx_has_bot_challenge retourneert False voor gewone pagina."""
    mock_resp = MagicMock()
    mock_resp.text = "<html><body><h1>Docs</h1></body></html>"

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await _httpx_has_bot_challenge("https://example.com")

    assert result is False


@pytest.mark.asyncio
async def test_wait_for_challenge_solved_returns_when_challenge_gone() -> None:
    """_wait_for_challenge_solved keert terug zodra de challenge weg is."""
    call_count = 0

    async def fake_content() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return "<title>Just a moment...</title><p>Enable JavaScript and cookies to continue</p>"
        return "<html><body><h1>Docs</h1></body></html>"

    mock_page = AsyncMock()
    mock_page.content = AsyncMock(side_effect=fake_content)

    await _wait_for_challenge_solved(mock_page, timeout=10)
    assert call_count >= 3


@pytest.mark.asyncio
async def test_scrape_pages_detects_bot_challenge_and_opens_headed() -> None:
    """scrape_pages opent headed browser als bot-bescherming gedetecteerd wordt."""
    mock_page = _make_mock_page()
    # Eerste aanroep (Fase 0.5 challenge check) → challenge-pagina,
    # zodat _is_bot_challenge True retourneert en _wait_for_challenge_solved aangeroepen wordt.
    # Tweede aanroep (scrapen zelf) → gewone HTML.
    mock_page.content = AsyncMock(
        side_effect=[
            "Just a moment... Enable JavaScript and cookies to continue",
            "<html><body><h1>Docs</h1></body></html>",
        ]
    )
    mock_pw, _ = _make_mock_playwright(mock_page)

    launched_headless_values: list[bool] = []

    original_launch = mock_pw.chromium.launch

    async def capturing_launch(**kwargs: object) -> object:
        launched_headless_values.append(kwargs.get("headless", True))
        return await original_launch(**kwargs)

    mock_pw.chromium.launch = AsyncMock(side_effect=capturing_launch)

    with (
        patch("src.scraper.async_playwright", return_value=mock_pw),
        patch("src.scraper.apply_login"),
        patch("src.scraper._httpx_has_bot_challenge", return_value=True),
        patch("src.scraper._wait_for_challenge_solved"),
    ):
        login_strategy = _make_login_strategy(mode="none")
        await scrape_pages(
            ["https://docs.example.com/page"],
            login_strategy,
            headless=True,
            start_url="https://docs.example.com",
        )

    assert launched_headless_values == [False]


@pytest.mark.parametrize(
    "urls,expected",
    [
        (
            [
                "https://x.com/display/API/Page+One",
                "https://x.com/display/API/Page+Two",
            ],
            "/display/API",
        ),
        (["https://x.com/a/b", "https://x.com/c/d"], ""),
        (["https://x.com/", ""], ""),
        (["https://x.com/Docs/a", "https://x.com/docs/b"], "/Docs"),
    ],
)
def test_common_path_prefix(urls: list[str], expected: str) -> None:
    """_common_path_prefix geeft het diepste gedeelde pad (hoofdletterongevoelig)."""
    from src.scraper import _common_path_prefix

    assert _common_path_prefix(urls) == expected


@pytest.mark.asyncio
async def test_wait_for_js_content_returns_when_link_count_stable() -> None:
    """_wait_for_js_content stopt zodra het aantal links stabiel blijft."""
    from src.scraper import _wait_for_js_content

    page = AsyncMock()
    page.eval_on_selector_all = AsyncMock(side_effect=[1, 5, 5, 5, 5, 5])

    await _wait_for_js_content(page, stable_for=0.0, timeout=5.0)

    # 1 → 5 verandert, daarna is 5 direct "stabiel" bij stable_for=0
    assert page.eval_on_selector_all.await_count == 3


@pytest.mark.asyncio
async def test_claude_identify_nav_links_parses_fenced_json() -> None:
    """Claude's antwoord in een ```json fence wordt geparsed tot een URL-lijst."""
    from src.scraper import _claude_identify_nav_links

    message = MagicMock()
    message.content = [
        MagicMock(text='```json\n["https://x.com/display/API/A", 42]\n```')
    ]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=message)

    with patch("anthropic.AsyncAnthropic", return_value=client):
        result = await _claude_identify_nav_links(
            ["https://x.com/display/API/A", "https://x.com/login"],
            "https://x.com/space/API",
        )

    assert result == ["https://x.com/display/API/A"]


@pytest.mark.asyncio
async def test_claude_identify_nav_links_returns_empty_on_error() -> None:
    """Bij een API-fout of zonder links geeft de functie een lege lijst."""
    from src.scraper import _claude_identify_nav_links

    assert await _claude_identify_nav_links([], "https://x.com") == []

    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("anthropic.AsyncAnthropic", return_value=client):
        assert (
            await _claude_identify_nav_links(["https://x.com/a"], "https://x.com") == []
        )
