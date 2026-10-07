"""Integratietests: docsnap-lookup tegen de lokale testsite, met echte headless Chromium."""

import asyncio
from contextlib import asynccontextmanager

import pytest
import yaml
from click.testing import CliRunner

from src.lookup import session
from src.lookup.cli import cli
from src.lookup.site import Site


def run(*args: str):
    return CliRunner().invoke(cli, ["--session", "test", *args])


@pytest.fixture
def logged_in(docsite, monkeypatch) -> Site:
    """Bron 'testsite', ingelogd via form-login met gegevens uit de omgeving."""
    assert run("init", f"{docsite}/docs/late", "--name", "testsite").exit_code == 3
    site = Site.load("testsite")
    site.cfg["login"]["mode"] = "form"
    site.save_cfg()
    monkeypatch.setenv("DOC_LOOKUP_TESTSITE_USER", "jeroen")
    monkeypatch.setenv("DOC_LOOKUP_TESTSITE_PASS", "geheim")
    result = run("login", "testsite")
    assert result.exit_code == 0, result.output
    return Site.load("testsite")


def test_init_detects_login_wall(docsite) -> None:
    result = run("init", f"{docsite}/docs/late", "--name", "testsite")

    assert result.exit_code == 3
    assert "Login vereist (wachtwoordveld zichtbaar)" in result.output
    assert "login testsite" in result.output
    cfg = yaml.safe_load(Site("testsite").cfg_path.read_text(encoding="utf-8"))
    assert cfg["login"] == {"mode": "manual", "login_url": f"{docsite}/login"}
    assert result.output.strip().splitlines()[-1].startswith("[tokens] deze stap ~")


def test_init_public_page_picks_content_container(docsite) -> None:
    result = run("init", f"{docsite}/open/start", "--name", "open")

    assert result.exit_code == 0, result.output
    assert "geen login nodig" in result.output
    assert "-> main.article-body" in result.output
    site = Site.load("open")
    assert site.get("content.selector") == "main.article-body"
    assert site.get("login.mode") == "none"
    assert (site.debug_dir / "init.png").exists()

    again = run("init", f"{docsite}/open/start", "--name", "open")
    assert "bestaat al" in again.output


def test_init_detects_soft_login_wall_behind_cookie_dialog(docsite) -> None:
    """Geen redirect of wachtwoordveld, alleen een melding (zoals MendriX)."""
    result = run("init", f"{docsite}/soft/page", "--name", "soft")

    assert result.exit_code == 3
    assert "Login vereist (melding 'Inloggen vereist')" in result.output
    # Geen redirect: de documentatiepagina zelf is geen loginpagina
    assert Site.load("soft").cfg["login"] == {"mode": "manual"}


def test_init_ignores_cookie_dialog_as_content(docsite) -> None:
    result = run("init", f"{docsite}/open/custom", "--name", "custom")

    assert result.exit_code == 0, result.output
    assert Site.load("custom").get("content.selector") == "div.kb-text"
    assert "#cookie_dialog" not in result.output
    assert "p-dialog-mask" not in result.output


def test_login_with_form_saves_session(logged_in: Site) -> None:
    assert logged_in.has_auth()
    assert "session" in logged_in.auth_path.read_text(encoding="utf-8")


def test_fetch_late_loading_page_completely(docsite, logged_in: Site) -> None:
    assert (
        run("init", f"{docsite}/docs/late", "--name", "testsite", "--refresh").exit_code
        == 0
    )
    assert Site.load("testsite").get("content.selector") == "main.article-body"

    result = run("fetch", "testsite", f"{docsite}/docs/late", f"{docsite}/docs/static")

    assert result.exit_code == 0, result.output
    assert "volledigheid: tekst 100.0%" in result.output
    assert "afb. 1/1  OK" in result.output
    assert "2 nieuw" in result.output
    site = Site.load("testsite")
    entry = site.index[f"{docsite}/docs/late"]
    md = (site.pages_dir / f"{entry['slug']}.md").read_text(encoding="utf-8")
    assert "# Gebeurtenis instellen" in md
    assert "> **Waarschuwing:**" in md
    assert "| Code | Unieke code (max 10 tekens) |" in md
    assert "```sql" in md
    assert "Laden..." not in md  # het laadscherm is niet opgeslagen
    image = entry["images"][0]
    assert (image["w"], image["h"]) == (640, 360)
    assert f"](../images/{entry['slug']}/" in md

    again = run("fetch", "testsite", f"{docsite}/docs/late", "--no-images")
    assert "gewijzigd" in again.output  # zonder afbeelding is de inhoud anders


def test_fetch_drops_sticky_table_copy_and_ignores_lazy_img_loader(docsite) -> None:
    import time

    assert run("init", f"{docsite}/open/sticky", "--name", "open").exit_code == 0

    t0 = time.monotonic()
    result = run("fetch", "open", f"{docsite}/open/sticky")

    assert time.monotonic() - t0 < 15  # niet tot STABLE_TIMEOUT (20s) wachten
    assert "tabellen 1/1" in result.output
    site = Site.load("open")
    md = (
        site.pages_dir / f"{site.index[f'{docsite}/open/sticky']['slug']}.md"
    ).read_text(encoding="utf-8")
    assert md.count("| Veld | Uitleg |") == 1
    assert "[afbeelding niet opgehaald: " in md  # mislukte download wordt gemeld
    assert "1 afbeelding(en) niet opgehaald" in result.output


def test_fetch_reports_missing_and_empty_pages(
    docsite, logged_in: Site, monkeypatch
) -> None:
    monkeypatch.setattr("src.lookup.render.STABLE_TIMEOUT", 1.0)

    result = run(
        "fetch", "testsite", f"{docsite}/docs/bestaatniet", f"{docsite}/docs/empty"
    )

    assert result.exit_code == 5
    assert "HTTP 404" in result.output
    assert "pagina bleef leeg (0 tekens na 2 pogingen)" in result.output
    assert "2 met problemen" in result.output


def test_fetch_structure_change_exits_4(docsite, logged_in: Site, monkeypatch) -> None:
    monkeypatch.setattr("src.lookup.render.STABLE_TIMEOUT", 1.0)
    logged_in.cfg["content"] = {"selector": "#bestaat-niet", "min_chars": 50}
    logged_in.save_cfg()

    result = run("fetch", "testsite", f"{docsite}/docs/static")

    assert result.exit_code == 4
    assert "kandidaat: main.article-body" in result.output
    assert "Herken opnieuw met: init" in result.output


def test_fetch_with_expired_session_exits_3(docsite, logged_in: Site) -> None:
    logged_in.auth_path.unlink()

    result = run("fetch", "testsite", f"{docsite}/docs/static")

    assert result.exit_code == 3
    assert "Voer uit: login testsite" in result.output


def test_sites_and_unknown_site(docsite, logged_in: Site) -> None:
    assert "Nog geen" not in run("sites").output
    output = run("sites").output
    assert f"- testsite: {docsite}/docs/late" in output
    assert "sessie opgeslagen" in output

    unknown = run("fetch", "onbekend", "https://x.com")
    assert unknown.exit_code == 2
    assert "Bekende bronnen: testsite" in unknown.output


def test_sites_empty() -> None:
    assert "Nog geen documentatiebronnen" in run("sites").output


def test_interactive_login_detects_success_without_enter(docsite, monkeypatch) -> None:
    """De gebruiker logt in het venster in; het script ziet zelf dat het gelukt is."""
    assert run("init", f"{docsite}/docs/late", "--name", "testsite").exit_code == 3
    real_open = session.open_context

    async def user_logs_in(page) -> None:
        await page.wait_for_selector("input[type=password]")
        await page.fill("input[name=user]", "jeroen")
        await page.fill("input[type=password]", "geheim")
        await page.click("button[type=submit]")

    @asynccontextmanager
    async def headless_with_user(site, headless=True, use_auth=True):
        async with real_open(site, headless=True, use_auth=use_auth) as context:
            context.on("page", lambda page: asyncio.ensure_future(user_logs_in(page)))
            yield context

    monkeypatch.setattr(session, "open_context", headless_with_user)

    result = run("login", "testsite", "--timeout", "30")

    assert result.exit_code == 0, result.output
    assert "Er opent een browservenster" in result.output
    assert Site.load("testsite").has_auth()


def test_interactive_login_times_out(docsite, monkeypatch) -> None:
    assert run("init", f"{docsite}/docs/late", "--name", "testsite").exit_code == 3
    real_open = session.open_context

    @asynccontextmanager
    async def headless(site, headless=True, use_auth=True):
        async with real_open(site, headless=True, use_auth=use_auth) as context:
            yield context

    monkeypatch.setattr(session, "open_context", headless)

    result = run("login", "testsite", "--timeout", "2")

    assert result.exit_code == 3
    assert "geen geslaagde login" in result.output
