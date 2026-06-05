"""
Detecteer Swagger/OpenAPI documentatiesites en haal de raw spec op.

Twee detectie-paden:
1. detect_and_fetch_swagger  — snel, via httpx, geen browser nodig.
   Werkt voor publieke Swagger UI / Redoc pagina's.
2. detect_swagger_in_page   — via een geladen Playwright-pagina.
   Werkt voor: (a) swagger achter login, (b) Confluence-pagina's met een
   embedded swagger-macro (com.confluence.swagger.api.document).
"""
from __future__ import annotations

import json
import re

import httpx

# Bekende OpenAPI spec endpoints
OPENAPI_CANDIDATES = [
    "/openapi.json",
    "/openapi.yaml",
    "/swagger.json",
    "/swagger.yaml",
    "/api-docs",
    "/api-docs/swagger.json",
    "/v2/api-docs",
    "/v3/api-docs",
    "/api/openapi.json",
    "/api/swagger.json",
]

# Patronen die wijzen op Swagger UI of Redoc in HTML
SWAGGER_PATTERNS = [
    re.compile(r"swagger[-_]ui", re.IGNORECASE),
    re.compile(r"swagger-ui\.js", re.IGNORECASE),
    re.compile(r"redoc\.standalone\.js", re.IGNORECASE),
    re.compile(r'"openapi"\s*:\s*"3\.[0-9]', re.IGNORECASE),
    re.compile(r'"swagger"\s*:\s*"2\.0"', re.IGNORECASE),
]


async def detect_and_fetch_swagger(base_url: str) -> dict | None:
    """Detecteer en haal OpenAPI/Swagger spec op als aanwezig.

    Returns:
        De geparsede spec als dict, of None als het geen Swagger site is.
    """
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            home_resp = await client.get(base_url)
            if _has_swagger_ui(home_resp.text):
                spec_url = _extract_spec_url(home_resp.text, origin)
                if spec_url:
                    result = await _fetch_spec(client, spec_url)
                    if result:
                        return result
        except httpx.RequestError:
            return None

        for path in OPENAPI_CANDIDATES:
            spec_url = f"{origin}{path}"
            result = await _fetch_spec(client, spec_url)
            if result:
                return result

    return None


def _has_swagger_ui(html: str) -> bool:
    """Controleer of de HTML Swagger UI of Redoc bevat."""
    return any(pattern.search(html) for pattern in SWAGGER_PATTERNS)


def _extract_spec_url(html: str, origin: str) -> str | None:
    """Probeer de spec URL te extraheren uit de Swagger UI HTML."""
    match = re.search(r'url:\s*["\']([^"\']+\.(?:json|yaml))["\']', html)
    if match:
        spec_path = match.group(1)
        return spec_path if spec_path.startswith("http") else f"{origin}{spec_path}"
    return None


async def _fetch_spec(client: httpx.AsyncClient, url: str) -> dict | None:
    """Haal een OpenAPI spec op en parse als JSON of YAML."""
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None

        content_type = resp.headers.get("content-type", "")

        if "json" in content_type or url.endswith(".json"):
            data = resp.json()
            if _is_valid_openapi_spec(data):
                return {"url": url, "format": "json", "spec": data}

        elif "yaml" in content_type or url.endswith((".yaml", ".yml")):
            try:
                import yaml  # type: ignore[import]

                data = yaml.safe_load(resp.text)
                if _is_valid_openapi_spec(data):
                    return {"url": url, "format": "yaml", "spec": data}
            except ImportError:
                return {"url": url, "format": "yaml_raw", "spec": resp.text}

    except (httpx.RequestError, ValueError):
        pass

    return None


def _is_valid_openapi_spec(data: object) -> bool:
    """Controleer of het object een geldig OpenAPI/Swagger spec is."""
    return isinstance(data, dict) and ("openapi" in data or "swagger" in data)


# ---------------------------------------------------------------------------
# Playwright-gebaseerde detectie (voor auth sites en Confluence embeds)
# ---------------------------------------------------------------------------

class SwaggerDetected(Exception):
    """Raised vanuit scraper wanneer een swagger spec gevonden is na login."""

    def __init__(self, result: dict) -> None:
        self.result = result


async def detect_swagger_in_page(page: object) -> dict | None:
    """Detecteer Swagger/OpenAPI spec via een geladen Playwright-pagina.

    Dekt twee gevallen:
    - Confluence-pagina met embedded swagger macro (Atlassian Connect)
    - Reguliere Swagger UI / Redoc pagina achter login

    Args:
        page: Playwright Page object, al genavigeerd naar de doelpagina.

    Returns:
        Spec-dict (url, format, spec) of None als geen swagger gevonden.
    """
    from urllib.parse import urlparse

    html: str = await page.content()  # type: ignore[attr-defined]

    if "com.confluence.swagger.api.document" in html:
        return await _fetch_confluence_swagger(page, html)

    if _has_swagger_ui(html):
        parsed = urlparse(page.url)  # type: ignore[attr-defined]
        origin = f"{parsed.scheme}://{parsed.netloc}"
        spec_url = _extract_spec_url(html, origin)
        if spec_url:
            try:
                resp = await page.request.fetch(spec_url)  # type: ignore[attr-defined]
                if resp.ok:
                    data = json.loads(await resp.text())
                    if _is_valid_openapi_spec(data):
                        return {"url": spec_url, "format": "json", "spec": data}
            except Exception:  # noqa: BLE001
                pass

    return None


async def _fetch_confluence_swagger(page: object, html: str) -> dict | None:
    """Haal OpenAPI spec op via de Confluence REST API.

    Gebruikt page.request zodat de browser-sessiecookies meegestuurd worden.
    """
    from urllib.parse import urlparse

    page_id = _extract_confluence_page_id(page.url, html)  # type: ignore[attr-defined]
    if not page_id:
        return None

    parsed = urlparse(page.url)  # type: ignore[attr-defined]
    api_url = (
        f"{parsed.scheme}://{parsed.netloc}"
        f"/wiki/rest/api/content/{page_id}?expand=body.storage"
    )

    try:
        resp = await page.request.fetch(api_url)  # type: ignore[attr-defined]
        if not resp.ok:
            return None
        body = await resp.json()
        storage_value = body.get("body", {}).get("storage", {}).get("value", "")
        spec = _extract_spec_from_confluence_storage(storage_value)
        if spec and _is_valid_openapi_spec(spec):
            return {"url": api_url, "format": "json", "spec": spec}
    except Exception:  # noqa: BLE001
        pass

    return None


def _extract_confluence_page_id(url: str, html: str) -> str | None:
    """Extraheer Confluence pageId uit de URL of de HTML."""
    match = re.search(r"/pages/(\d+)", url)
    if match:
        return match.group(1)
    match = re.search(r'"content\.id"\s*:\s*"(\d+)"', html)
    if match:
        return match.group(1)
    return None


def _extract_spec_from_confluence_storage(storage_xml: str) -> dict | None:
    """Extraheer OpenAPI JSON uit Confluence storage-format XML.

    De spec zit in een <ac:plain-text-body> CDATA-blok van de swagger macro.
    """
    match = re.search(
        r"<ac:plain-text-body><!\[CDATA\[(.*?)\]\]></ac:plain-text-body>",
        storage_xml,
        re.DOTALL,
    )
    if match:
        try:
            return json.loads(match.group(1).strip())
        except (ValueError, json.JSONDecodeError):
            pass
    return None
