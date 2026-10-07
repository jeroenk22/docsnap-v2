"""Tests voor Swagger/OpenAPI detectie."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.swagger import (
    SwaggerDetected,
    _extract_confluence_page_id,
    _extract_spec_from_confluence_storage,
    _extract_spec_url,
    _fetch_spec,
    _has_swagger_ui,
    _is_valid_openapi_spec,
    detect_and_fetch_swagger,
    detect_swagger_in_page,
)


def test_has_swagger_ui_detects_swagger_ui():
    """Swagger UI JS-include wordt herkend."""
    html = '<script src="https://unpkg.com/swagger-ui-dist/swagger-ui.js"></script>'
    assert _has_swagger_ui(html)


def test_has_swagger_ui_detects_redoc():
    """Redoc standalone JS wordt herkend."""
    html = '<script src="https://cdn.jsdelivr.net/npm/redoc.standalone.js"></script>'
    assert _has_swagger_ui(html)


def test_has_swagger_ui_detects_inline_openapi():
    """Inline OpenAPI JSON in HTML wordt herkend."""
    html = 'const spec = {"openapi": "3.0.0", "info": {}};'
    assert _has_swagger_ui(html)


def test_has_swagger_ui_no_swagger():
    """Gewone HTML zonder Swagger wordt NIET herkend."""
    html = "<html><body><p>Gewone documentatie</p></body></html>"
    assert not _has_swagger_ui(html)


def test_is_valid_openapi_spec_v3():
    """OpenAPI 3.x spec is geldig."""
    assert _is_valid_openapi_spec({"openapi": "3.0.0", "info": {"title": "Test API"}})


def test_is_valid_openapi_spec_swagger_v2():
    """Swagger 2.0 spec is geldig."""
    assert _is_valid_openapi_spec({"swagger": "2.0", "info": {}})


def test_is_valid_openapi_spec_invalid_dict():
    """Dict zonder openapi/swagger sleutels is ongeldig."""
    assert not _is_valid_openapi_spec({"title": "Geen spec"})


def test_is_valid_openapi_spec_non_dict():
    """Niet-dict waardes zijn ongeldig."""
    assert not _is_valid_openapi_spec("string")  # type: ignore[arg-type]
    assert not _is_valid_openapi_spec([])  # type: ignore[arg-type]
    assert not _is_valid_openapi_spec(None)  # type: ignore[arg-type]


def test_extract_spec_url_from_swagger_ui_config():
    """Spec URL wordt geëxtraheerd uit Swagger UI HTML."""
    html = "SwaggerUIBundle({ url: '/api/openapi.json', dom_id: '#swagger' })"
    result = _extract_spec_url(html, "https://api.example.com")
    assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests voor Confluence-specifieke helpers
# ---------------------------------------------------------------------------

def test_extract_confluence_page_id_from_url():
    """pageId wordt correct uit een Confluence Cloud URL gehaald."""
    url = "https://mendrix.atlassian.net/wiki/spaces/MAD/pages/1866694660/2025.3"
    assert _extract_confluence_page_id(url, "") == "1866694660"


def test_extract_confluence_page_id_from_html_fallback():
    """pageId wordt uit de HTML gehaald als de URL geen /pages/{id} bevat."""
    html = '"content.id":"9876543210"'
    assert _extract_confluence_page_id("https://example.com/wiki", html) == "9876543210"


def test_extract_confluence_page_id_not_found():
    """Geeft None terug als er geen pageId te vinden is."""
    assert _extract_confluence_page_id("https://example.com", "") is None


def test_extract_spec_from_confluence_storage_valid():
    """OpenAPI JSON wordt correct uit Confluence storage XML geëxtraheerd."""
    spec = {"openapi": "3.0.3", "info": {"title": "Test API"}}
    storage_xml = (
        '<ac:structured-macro ac:name="swagger-integration">'
        "<ac:plain-text-body><![CDATA["
        + json.dumps(spec)
        + "]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )
    result = _extract_spec_from_confluence_storage(storage_xml)
    assert result == spec


def test_extract_spec_from_confluence_storage_no_cdata():
    """Geeft None terug als er geen CDATA-blok aanwezig is."""
    assert _extract_spec_from_confluence_storage("<ac:structured-macro/>") is None


def test_extract_spec_from_confluence_storage_invalid_json():
    """Geeft None terug als de CDATA geen geldig JSON bevat."""
    storage_xml = "<ac:plain-text-body><![CDATA[niet-json]]></ac:plain-text-body>"
    assert _extract_spec_from_confluence_storage(storage_xml) is None


# ---------------------------------------------------------------------------
# Tests voor detect_swagger_in_page
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_swagger_in_page_confluence_macro() -> None:
    """detect_swagger_in_page herkent Confluence embedded swagger en haalt spec op."""
    spec = {"openapi": "3.0.3", "info": {"title": "MendriX API"}, "paths": {}}
    storage_xml = (
        '<ac:plain-text-body><![CDATA[' + json.dumps(spec) + ']]></ac:plain-text-body>'
    )
    confluence_response = {
        "body": {"storage": {"value": storage_xml}}
    }

    mock_api_resp = AsyncMock()
    mock_api_resp.ok = True
    mock_api_resp.json = AsyncMock(return_value=confluence_response)

    mock_request = AsyncMock()
    mock_request.fetch = AsyncMock(return_value=mock_api_resp)

    mock_page = MagicMock()
    mock_page.url = "https://mendrix.atlassian.net/wiki/spaces/MAD/pages/1866694660/2025.3"
    mock_page.content = AsyncMock(
        return_value='<html><body id="com.confluence.swagger.api.document"></body></html>'
    )
    mock_page.request = mock_request

    result = await detect_swagger_in_page(mock_page)

    assert result is not None
    assert result["spec"] == spec
    assert "1866694660" in result["url"]


@pytest.mark.asyncio
async def test_detect_swagger_in_page_returns_none_for_plain_html() -> None:
    """detect_swagger_in_page geeft None terug voor gewone HTML zonder swagger."""
    mock_page = MagicMock()
    mock_page.url = "https://docs.example.com/guide"
    mock_page.content = AsyncMock(return_value="<html><body><p>Docs</p></body></html>")

    result = await detect_swagger_in_page(mock_page)
    assert result is None


@pytest.mark.asyncio
async def test_detect_swagger_in_page_swagger_ui_with_spec_url() -> None:
    """detect_swagger_in_page haalt spec op van een Swagger UI pagina via page.request."""
    spec = {"openapi": "3.0.3", "info": {"title": "Test API"}, "paths": {}}
    spec_json = json.dumps(spec)

    mock_fetch_resp = AsyncMock()
    mock_fetch_resp.ok = True
    mock_fetch_resp.text = AsyncMock(return_value=spec_json)

    mock_request = AsyncMock()
    mock_request.fetch = AsyncMock(return_value=mock_fetch_resp)

    swagger_html = (
        '<html><body>'
        '<script src="https://unpkg.com/swagger-ui-dist/swagger-ui.js"></script>'
        "SwaggerUIBundle({ url: '/api/openapi.json' })"
        '</body></html>'
    )

    mock_page = MagicMock()
    mock_page.url = "https://api.example.com/docs"
    mock_page.content = AsyncMock(return_value=swagger_html)
    mock_page.request = mock_request

    result = await detect_swagger_in_page(mock_page)

    assert result is not None
    assert result["spec"] == spec


# ---------------------------------------------------------------------------
# Tests voor detect_and_fetch_swagger (httpx-gebaseerd)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_and_fetch_swagger_returns_none_for_plain_site() -> None:
    """detect_and_fetch_swagger geeft None terug als er geen swagger is."""
    mock_resp = MagicMock()
    mock_resp.text = "<html><body><p>Gewone docs</p></body></html>"
    mock_resp.status_code = 404

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.swagger.httpx.AsyncClient", return_value=mock_client):
        result = await detect_and_fetch_swagger("https://docs.example.com")

    assert result is None


@pytest.mark.asyncio
async def test_detect_and_fetch_swagger_finds_spec_via_candidate() -> None:
    """detect_and_fetch_swagger vindt een spec via een bekende kandidaat-URL."""
    spec = {"openapi": "3.0.3", "info": {"title": "My API"}}

    plain_resp = MagicMock()
    plain_resp.text = "<html><body>no swagger ui</body></html>"
    plain_resp.status_code = 200

    spec_resp = MagicMock()
    spec_resp.status_code = 200
    spec_resp.headers = {"content-type": "application/json"}
    spec_resp.json = MagicMock(return_value=spec)

    not_found_resp = MagicMock()
    not_found_resp.status_code = 404

    call_count = 0

    async def fake_get(url: str, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return plain_resp
        if url.endswith("/openapi.json"):
            return spec_resp
        return not_found_resp

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.swagger.httpx.AsyncClient", return_value=mock_client):
        result = await detect_and_fetch_swagger("https://api.example.com")

    assert result is not None
    assert result["spec"] == spec


@pytest.mark.asyncio
async def test_detect_and_fetch_swagger_request_error_returns_none() -> None:
    """detect_and_fetch_swagger geeft None terug bij netwerk-fout."""
    import httpx

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.RequestError("timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.swagger.httpx.AsyncClient", return_value=mock_client):
        result = await detect_and_fetch_swagger("https://api.example.com")

    assert result is None


# ---------------------------------------------------------------------------
# Tests voor _fetch_spec
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_spec_json_valid() -> None:
    """_fetch_spec retourneert spec dict voor geldige JSON response."""
    spec = {"openapi": "3.0.3", "info": {"title": "Test"}}

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json = MagicMock(return_value=spec)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    result = await _fetch_spec(mock_client, "https://example.com/openapi.json")

    assert result is not None
    assert result["spec"] == spec
    assert result["format"] == "json"


@pytest.mark.asyncio
async def test_fetch_spec_returns_none_for_404() -> None:
    """_fetch_spec geeft None terug als de server 404 antwoordt."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    result = await _fetch_spec(mock_client, "https://example.com/missing.json")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_spec_returns_none_for_invalid_spec() -> None:
    """_fetch_spec geeft None terug als de JSON geen geldige OpenAPI spec is."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json = MagicMock(return_value={"not": "a spec"})

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    result = await _fetch_spec(mock_client, "https://example.com/openapi.json")

    assert result is None


def test_swagger_detected_exception_stores_result() -> None:
    """SwaggerDetected slaat het resultaat op als attribuut."""
    result = {"url": "https://example.com/openapi.json", "format": "json", "spec": {}}
    exc = SwaggerDetected(result)
    assert exc.result == result


@pytest.mark.asyncio
async def test_fetch_spec_yaml_without_pyyaml_rejects_html() -> None:
    """Zonder PyYAML mag een HTML-redirect niet als YAML-spec worden geaccepteerd.

    Regressie: /swagger.yaml redirectte naar een Scalar docs-pagina; de
    yaml_raw-fallback sloeg die HTML op als 'spec'.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "text/html"}
    mock_resp.text = "<!doctype html>\n<html><body><div id='app'></div></body></html>"

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.dict("sys.modules", {"yaml": None}):
        result = await _fetch_spec(mock_client, "https://example.com/swagger.yaml")

    assert result is None


@pytest.mark.asyncio
async def test_fetch_spec_yaml_without_pyyaml_accepts_real_spec() -> None:
    """Zonder PyYAML wordt echte YAML-spec-tekst wel als yaml_raw geaccepteerd."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/yaml"}
    mock_resp.text = "openapi: 3.0.3\ninfo:\n  title: Test\n"

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch.dict("sys.modules", {"yaml": None}):
        result = await _fetch_spec(mock_client, "https://example.com/swagger.yaml")

    assert result is not None
    assert result["format"] == "yaml_raw"


@pytest.mark.asyncio
async def test_detect_and_fetch_swagger_start_url_is_the_spec() -> None:
    """De start-URL kan zelf de spec zijn (bijv. .../openapi/pro-v1.json).

    Regressie: die response werd genegeerd, waarna de kandidaten-loop een
    verkeerde URL oppikte.
    """
    spec = {"openapi": "3.1.1", "info": {"title": "Pro - API V1", "version": "v1"}}

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json;charset=utf-8"}
    mock_resp.json = MagicMock(return_value=spec)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("src.swagger.httpx.AsyncClient", return_value=mock_client):
        result = await detect_and_fetch_swagger("https://example.com/openapi/pro-v1.json")

    assert result is not None
    assert result["spec"] == spec
    assert result["url"] == "https://example.com/openapi/pro-v1.json"
    # Alleen de start-URL opgehaald: geen doorval naar de kandidaten-loop.
    assert mock_client.get.await_count == 1


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/dev/api-docs", True),
        ("https://example.com/swagger/index.html", True),
        ("https://example.com/guide/intro", False),
        # Subdomeinen als docs.* of api.* tellen niet: alleen het pad.
        ("https://docs.example.com", False),
        ("https://api.example.com/", False),
    ],
)
def test_url_looks_like_api_portal(url: str, expected: bool) -> None:
    """Alleen het URL-pad bepaalt of het op een API-portal lijkt."""
    from src.swagger import _url_looks_like_api_portal

    assert _url_looks_like_api_portal(url) is expected


def _fake_playwright(responses: list[MagicMock]) -> MagicMock:
    """Bouw een async_playwright()-mock die bij goto de responses afvuurt."""
    handlers: list = []

    page = MagicMock()
    page.on = MagicMock(side_effect=lambda event, fn: handlers.append(fn))

    async def fake_goto(url: str, **kwargs: object) -> None:
        for resp in responses:
            for fn in handlers:
                await fn(resp)

    page.goto = AsyncMock(side_effect=fake_goto)

    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    pw = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser)
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=pw)
    manager.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=manager)


def _json_response(url: str, data: object, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.url = url
    resp.status = status
    resp.headers = {"content-type": "application/json"}
    resp.json = AsyncMock(return_value=data)
    return resp


@pytest.mark.asyncio
async def test_detect_via_browser_intercept_captures_spec() -> None:
    """De eerste JSON-response die een OpenAPI spec is, wordt teruggegeven."""
    from src.swagger import _detect_via_browser_intercept

    spec = {"openapi": "3.0.0", "info": {"title": "X"}}
    responses = [
        _json_response("https://example.com/config.json", {"theme": "dark"}),
        _json_response("https://cdn.example.com/spec.json", spec),
    ]

    with (
        patch("playwright.async_api.async_playwright", _fake_playwright(responses)),
        patch("asyncio.sleep", AsyncMock()),
    ):
        result = await _detect_via_browser_intercept("https://example.com/dev")

    assert result == {"url": "https://cdn.example.com/spec.json", "format": "json", "spec": spec}


@pytest.mark.asyncio
async def test_detect_via_browser_intercept_returns_none_without_spec() -> None:
    """Zonder spec-response geeft de interceptie None terug."""
    from src.swagger import _detect_via_browser_intercept

    responses = [_json_response("https://example.com/a.json", {"a": 1})]

    with (
        patch("playwright.async_api.async_playwright", _fake_playwright(responses)),
        patch("asyncio.sleep", AsyncMock()),
    ):
        result = await _detect_via_browser_intercept("https://example.com/dev")

    assert result is None


@pytest.mark.asyncio
async def test_detect_and_fetch_swagger_uses_browser_fallback_for_api_path() -> None:
    """Zonder httpx-resultaat valt een /dev-URL terug op browser-interceptie."""
    plain = MagicMock()
    plain.status_code = 404
    plain.text = "<html></html>"
    plain.headers = {}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=plain)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    found = {"url": "u", "format": "json", "spec": {"openapi": "3.0.0"}}
    with (
        patch("src.swagger.httpx.AsyncClient", return_value=mock_client),
        patch("src.swagger._detect_via_browser_intercept", AsyncMock(return_value=found)) as m,
    ):
        result = await detect_and_fetch_swagger("https://example.com/dev")

    m.assert_awaited_once_with("https://example.com/dev")
    assert result == found
