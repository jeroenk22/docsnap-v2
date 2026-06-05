"""
Detecteer Swagger/OpenAPI documentatiesites en haal de raw spec op.

Detectie:
- Controleer of de pagina Swagger UI of Redoc JS laadt
- Zoek naar /openapi.json, /swagger.json, /api-docs, /v2/api-docs, etc.
- Haal de spec op als JSON of YAML
"""
from __future__ import annotations

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
