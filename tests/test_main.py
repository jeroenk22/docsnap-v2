"""Tests voor de CLI entry point (src/main.py)."""

import pytest
from click.testing import CliRunner

from src.main import _project_name_from_url, cli


def test_cli_help():
    """CLI --help werkt correct."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "docsnap" in result.output.lower()


def test_cli_missing_url():
    """URL argument is verplicht."""
    runner = CliRunner()
    result = runner.invoke(cli, [])
    assert result.exit_code != 0


def test_cli_invalid_login_option():
    """Ongeldige login optie geeft foutmelding."""
    runner = CliRunner()
    result = runner.invoke(cli, ["https://example.com", "--login", "ssh"])
    assert result.exit_code != 0


def test_cli_invalid_output_option():
    """Ongeldig output formaat geeft foutmelding."""
    runner = CliRunner()
    result = runner.invoke(cli, ["https://example.com", "--output", "xml"])
    assert result.exit_code != 0


def test_cli_valid_options():
    """Geldige opties worden geaccepteerd (help werkt met alle opties)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert "--login" in result.output
    assert "--output" in result.output
    assert "--out-dir" in result.output


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://support.mendrix.nl/space/API", "API"),
        (
            "https://mendrix.atlassian.net/wiki/spaces/MAD/pages/1866694660/2025.3",
            "2025.3",
        ),
        ("https://docs.example.com/getting-started", "getting-started"),
        ("https://docs.example.com/", "docs"),
        ("https://docs.example.com", "docs"),
        ("https://example.com/path with spaces", "path-with-spaces"),
    ],
)
def test_project_name_from_url(url: str, expected: str) -> None:
    """URL wordt correct omgezet naar een veilige projectnaam."""
    assert _project_name_from_url(url) == expected


def test_cli_rejects_truncated_url():
    """Een URL die eindigt op + (afgekapt door de shell) geeft een UsageError."""
    runner = CliRunner()
    result = runner.invoke(cli, ["https://example.com/page?q=a+"])
    assert result.exit_code != 0
    assert "aanhalingstekens" in result.output


# ---------------------------------------------------------------------------
# Tests voor de _run flow (alle externe stappen gemockt)
# ---------------------------------------------------------------------------

PAGE = {"url": "https://docs.example.com/a", "title": "A", "html": "<p>a</p>"}
CLEANED = {"url": "https://docs.example.com/a", "title": "A", "markdown": "# A"}


def _patch_pipeline(swagger=None, discovered=None, scraped=None, cleaned=None):
    """Patch alle stappen van de pipeline; geeft de mocks terug als dict."""
    from unittest.mock import AsyncMock, MagicMock, patch

    mocks = {
        "swagger": AsyncMock(return_value=swagger),
        "discover": AsyncMock(return_value=discovered or []),
        "scrape": AsyncMock(return_value=scraped if scraped is not None else [PAGE]),
        "clean": AsyncMock(return_value=cleaned if cleaned is not None else [CLEANED]),
        "export": MagicMock(),
    }
    patches = [
        patch("src.swagger.detect_and_fetch_swagger", mocks["swagger"]),
        patch("src.discovery.discover_pages", mocks["discover"]),
        patch("src.scraper.scrape_pages", mocks["scrape"]),
        patch("src.cleaner.clean_pages", mocks["clean"]),
        patch("src.exporter.export", mocks["export"]),
    ]
    return mocks, patches


async def _run_with(tmp_path, patches, **kwargs):
    from contextlib import ExitStack

    from src.main import _run

    args = {
        "url": "https://docs.example.com",
        "login": "none",
        "username": None,
        "password": None,
        "output": "markdown",
        "out_dir": str(tmp_path),
        "single": False,
    }
    args.update(kwargs)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        await _run(**args)


@pytest.mark.asyncio
async def test_run_exports_swagger_and_skips_scraping(tmp_path) -> None:
    """Als er een spec gevonden wordt, wordt die geëxporteerd zonder te scrapen."""
    spec = {
        "url": "https://docs.example.com/openapi.json",
        "format": "json",
        "spec": {},
    }
    mocks, patches = _patch_pipeline(swagger=spec)

    await _run_with(tmp_path, patches)

    mocks["scrape"].assert_not_called()
    exported = mocks["export"].call_args.args[0]
    assert exported == {"swagger": spec}


@pytest.mark.asyncio
async def test_run_single_scrapes_only_given_url(tmp_path) -> None:
    """--single slaat discovery over en scrapet alleen de opgegeven URL."""
    mocks, patches = _patch_pipeline()

    await _run_with(tmp_path, patches, url="https://docs.example.com/a", single=True)

    mocks["discover"].assert_not_called()
    assert mocks["scrape"].call_args.args[0] == ["https://docs.example.com/a"]
    assert "start_url" not in mocks["scrape"].call_args.kwargs
    mocks["export"].assert_called_once()


@pytest.mark.asyncio
async def test_run_no_login_uses_discovered_pages(tmp_path) -> None:
    """Zonder login worden de via discovery gevonden pagina's gescrapet."""
    found = ["https://docs.example.com/a", "https://docs.example.com/b"]
    mocks, patches = _patch_pipeline(discovered=found)

    await _run_with(tmp_path, patches)

    assert mocks["scrape"].call_args.args[0] == found
    assert mocks["export"].call_args.args[0] == [CLEANED]


@pytest.mark.asyncio
async def test_run_no_login_falls_back_to_browser_discovery(tmp_path) -> None:
    """Vindt httpx-discovery ≤1 pagina, dan krijgt scrape_pages een lege lijst."""
    mocks, patches = _patch_pipeline(discovered=["https://docs.example.com"])

    await _run_with(tmp_path, patches)

    assert mocks["scrape"].call_args.args[0] == []
    assert mocks["scrape"].call_args.kwargs["start_url"] == "https://docs.example.com"


@pytest.mark.asyncio
async def test_run_login_exports_swagger_detected_after_login(tmp_path) -> None:
    """Een spec die pas na inloggen gevonden wordt, wordt geëxporteerd."""
    from src.swagger import SwaggerDetected

    spec = {
        "url": "https://docs.example.com/openapi.json",
        "format": "json",
        "spec": {},
    }
    mocks, patches = _patch_pipeline()
    mocks["scrape"].side_effect = SwaggerDetected(spec)

    await _run_with(tmp_path, patches, login="manual")

    mocks["clean"].assert_not_called()
    assert mocks["export"].call_args.args[0] == {"swagger": spec}


@pytest.mark.asyncio
async def test_run_retries_incomplete_pages(tmp_path) -> None:
    """Onvolledige pagina's worden opnieuw gescrapet en vervangen."""
    incomplete = dict(CLEANED, _incomplete=True)
    fixed = dict(CLEANED, markdown="# A volledig")
    mocks, patches = _patch_pipeline(discovered=["x", "y"])
    mocks["clean"].side_effect = [[incomplete], [fixed]]

    await _run_with(tmp_path, patches)

    retry_call = mocks["scrape"].call_args_list[1]
    assert retry_call.args[0] == [CLEANED["url"]]
    assert retry_call.kwargs["extra_wait"] == 3.0
    assert mocks["export"].call_args.args[0] == [fixed]
