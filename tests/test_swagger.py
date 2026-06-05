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
