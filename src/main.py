"""
CLI entry point voor docsnap-v2.

Gebruik:
    docsnap <url> [--login none|form|manual] [--user x] [--pass y] [--output markdown|files|pdf]
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()


@click.command()
@click.argument("url")
@click.option(
    "--login",
    type=click.Choice(["none", "form", "manual"]),
    default="none",
    show_default=True,
    help="Login strategie.",
)
@click.option(
    "--user", "username", default=None, help="Gebruikersnaam voor form-login."
)
@click.option("--pass", "password", default=None, help="Wachtwoord voor form-login.")
@click.option(
    "--output",
    type=click.Choice(["markdown", "files", "pdf"]),
    default="markdown",
    show_default=True,
    help="Outputformaat.",
)
@click.option(
    "--out-dir",
    default="./output",
    show_default=True,
    help="Map waar output wordt opgeslagen.",
)
@click.option(
    "--single",
    is_flag=True,
    default=False,
    help="Sla discovery over en scrape alleen de opgegeven URL.",
)
def cli(
    url: str,
    login: str,
    username: str | None,
    password: str | None,
    output: str,
    out_dir: str,
    single: bool,
) -> None:
    """docsnap — scrape documentatiesites naar schone Markdown.

    URL is de startpagina van de documentatie die je wilt scrapen.

    \b
    Let op: URLs met & moeten tussen aanhalingstekens staan, anders kapt de
    shell de URL af. Gebruik altijd:
        docsnap "https://example.com/page?a=1&b=2"
    """
    if url.endswith("+") or url.endswith("%2"):
        raise click.UsageError(
            "De URL lijkt afgekapt. Zet de URL tussen aanhalingstekens:\n"
            f'    docsnap "{url}..." --login ...\n'
            "URLs met & worden door de shell gesplitst als je ze niet quoot."
        )
    asyncio.run(_run(url, login, username, password, output, out_dir, single))


async def _run(
    url: str,
    login: str,
    username: str | None,
    password: str | None,
    output: str,
    out_dir: str,
    single: bool = False,
) -> None:
    from .cleaner import clean_pages
    from .discovery import discover_pages
    from .exporter import export
    from .login import create_login_strategy
    from .scraper import scrape_pages
    from .swagger import SwaggerDetected, detect_and_fetch_swagger

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    project_name = _project_name_from_url(url)
    click.echo(f"🔍  Doelsite: {url}")

    # Stap 1: detecteer Swagger/OpenAPI — als gevonden, geen HTML scraping nodig
    swagger_result = await detect_and_fetch_swagger(url)
    if swagger_result:
        click.echo("✅  Swagger/OpenAPI spec gevonden — sla HTML scraping over.")
        export({"swagger": swagger_result}, output, out_path, project_name)
        return

    # Stap 2: maak login strategie aan
    login_strategy = create_login_strategy(login, username, password)

    # Stap 3: ontdek alle pagina's
    if single:
        # --single: sla discovery over, scrape alleen de opgegeven URL.
        # Login werkt via urls[0]; start_url=None voorkomt browser-discovery.
        click.echo("📄  Enkele pagina — discovery overgeslagen.")
        raw_pages = await scrape_pages([url], login_strategy)
    elif login_strategy.mode == "none":
        click.echo("📡  Pagina's ontdekken...")
        pages = await discover_pages(url)
        if len(pages) <= 1:
            click.echo(
                "   → Geen sitemap gevonden — browser wordt gebruikt voor discovery."
            )
            pages = []
        else:
            click.echo(f"   → {len(pages)} pagina's gevonden.")

        # Stap 4a: scrape (zonder login)
        click.echo("🌐  Pagina's scrapen...")
        raw_pages = await scrape_pages(pages, login_strategy, start_url=url)
    else:  # login met discovery
        # Stap 3+4 gecombineerd: login → swagger check → discovery → scrapen
        pages = []
        try:
            raw_pages = await scrape_pages(pages, login_strategy, start_url=url)
        except SwaggerDetected as exc:
            click.echo(
                "✅  Swagger/OpenAPI spec gevonden na inloggen — sla scraping over."
            )
            click.echo(f"💾  Opslaan als {output} in {out_path}...")
            export({"swagger": exc.result}, output, out_path, project_name)
            click.echo("✅  Klaar!")
            return

    # Stap 5: clean content via Claude API
    click.echo(f"🤖  Content opschonen via Claude ({len(raw_pages)} pagina's)...")
    cleaned_pages = await clean_pages(raw_pages)

    # Stap 5b: herlaad pagina's die de completeness-check niet haalden
    incomplete_urls = [c["url"] for c in cleaned_pages if c.get("_incomplete")]
    if incomplete_urls:
        click.echo(
            f"🔄  {len(incomplete_urls)} mogelijk onvolledige pagina's opnieuw scrapen (extra wachttijd)..."
        )
        retried = await scrape_pages(incomplete_urls, login_strategy, extra_wait=3.0)
        if retried:
            retried_cleaned = await clean_pages(retried)
            retried_by_url = {c["url"]: c for c in retried_cleaned}
            cleaned_pages = [retried_by_url.get(c["url"], c) for c in cleaned_pages]

    # Stap 6: exporteer
    click.echo(f"💾  Opslaan als {output} in {out_path}...")
    export(cleaned_pages, output, out_path, project_name)
    click.echo("✅  Klaar!")


def _project_name_from_url(url: str) -> str:
    """Leid een leesbare projectnaam af van de start-URL."""
    from urllib.parse import unquote, urlparse

    p = urlparse(url)
    parts = [s for s in p.path.strip("/").split("/") if s]
    raw = unquote(parts[-1]) if parts else p.netloc.split(":")[0].split(".")[0]
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in raw)
    return safe.strip("-") or "documentation"


if __name__ == "__main__":
    cli()
