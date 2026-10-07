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

import contextlib
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

    Strategie:
    1. httpx — snel, geen browser. Werkt als de HTML swagger-patronen bevat
       en de spec-URL erin staat of via bekende paden vindbaar is.
    2. Browser-interceptie — fallback voor JS-rendered Swagger UIs (React/Vue).
       Playwright laadt de pagina en luistert naar JSON-responses die eruitzien
       als een OpenAPI spec, ongeacht waar de spec gehost is.

    Returns:
        De geparsede spec als dict, of None als het geen Swagger site is.
    """
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    swagger_hint = False  # httpx zag iets dat op swagger lijkt

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            home_resp = await client.get(base_url)
            # De start-URL kan zelf al de spec zijn (bijv. .../openapi/pro-v1.json).
            direct = _parse_spec_response(base_url, home_resp)
            if direct:
                return direct
            if _has_swagger_ui(home_resp.text):
                swagger_hint = True
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

    # Fallback: browser-interceptie voor JS-rendered Swagger UIs.
    # Alleen proberen als de httpx-HTML al een swagger-hint gaf, of als de
    # URL-structuur op een dev/api/docs portal lijkt (voorkomt onnodige
    # browser-starts voor gewone sites).
    if swagger_hint or _url_looks_like_api_portal(base_url):
        return await _detect_via_browser_intercept(base_url)

    return None


def _url_looks_like_api_portal(url: str) -> bool:
    """Snelle heuristiek: URL-pad suggereert een API/developer portal."""
    from urllib.parse import urlparse

    path = urlparse(url).path.lower()
    return any(
        kw in path for kw in ("/dev", "/api", "/docs", "/swagger", "/openapi", "/redoc")
    )


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
        return _parse_spec_response(url, resp)
    except (httpx.RequestError, ValueError):
        return None


def _parse_spec_response(url: str, resp: httpx.Response) -> dict | None:
    """Parse een HTTP-response als OpenAPI spec, of None als het er geen is."""
    if resp.status_code != 200:
        return None

    content_type = resp.headers.get("content-type", "")

    try:
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
                # Zonder PyYAML kunnen we niet parsen; check daarom tekstueel of
                # dit echt een spec is. Redirects naar een HTML docs-pagina
                # zouden anders als "spec" worden opgeslagen.
                if _looks_like_yaml_spec(resp.text):
                    return {"url": url, "format": "yaml_raw", "spec": resp.text}
    except ValueError:
        pass

    return None


def _is_valid_openapi_spec(data: object) -> bool:
    """Controleer of het object een geldig OpenAPI/Swagger spec is."""
    return isinstance(data, dict) and ("openapi" in data or "swagger" in data)


def _looks_like_yaml_spec(text: str) -> bool:
    """Ruwe check of onparsebare tekst een OpenAPI/Swagger YAML-spec is."""
    return re.search(r"^\s*(openapi|swagger)\s*:", text, re.MULTILINE) is not None


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


async def _detect_via_browser_intercept(url: str) -> dict | None:
    """Laad de pagina in Playwright en intercepteer het OpenAPI spec-request.

    Swagger UI (en Redoc) maken altijd een netwerkverzoek naar de spec zodra de
    pagina laadt. Door alle JSON-responses te monitoren vangen we de spec op
    zonder te weten waar die gehost is — werkt voor elke Swagger UI configuratie.
    """
    from playwright.async_api import async_playwright

    spec_found: list[dict] = []  # list zodat de closure kan schrijven

    async def _on_response(response: object) -> None:
        if spec_found:
            return
        try:
            ct = response.headers.get("content-type", "")  # type: ignore[attr-defined]
            if response.status == 200 and "json" in ct:  # type: ignore[attr-defined]
                data = await response.json()  # type: ignore[attr-defined]
                if _is_valid_openapi_spec(data):
                    spec_found.append(
                        {"url": response.url, "format": "json", "spec": data}
                    )  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            page.on("response", _on_response)
            with contextlib.suppress(Exception):
                await page.goto(url, wait_until="networkidle", timeout=30_000)
            # Korte extra wacht voor Swagger UIs die de spec lazily laden
            import asyncio

            await asyncio.sleep(2)
            await browser.close()
    except Exception:  # noqa: BLE001
        return None

    return spec_found[0] if spec_found else None


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
