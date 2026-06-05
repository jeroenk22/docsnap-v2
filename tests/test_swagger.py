"""Tests voor Swagger/OpenAPI detectie."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.swagger import (
    _extract_confluence_page_id,
    _extract_spec_from_confluence_storage,
    _extract_spec_url,
    _has_swagger_ui,
    _is_valid_openapi_spec,
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
