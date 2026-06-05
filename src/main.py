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
@click.option("--user", "username", default=None, help="Gebruikersnaam voor form-login.")
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
def cli(
    url: str,
    login: str,
    username: str | None,
    password: str | None,
    output: str,
    out_dir: str,
) -> None:
    """docsnap — scrape documentatiesites naar schone Markdown.

    URL is de startpagina van de documentatie die je wilt scrapen.
    """
    asyncio.run(_run(url, login, username, password, output, out_dir))


async def _run(
    url: str,
    login: str,
    username: str | None,
    password: str | None,
    output: str,
    out_dir: str,
) -> None:
    from .cleaner import clean_pages
    from .discovery import discover_pages
    from .exporter import export
    from .login import create_login_strategy
    from .scraper import scrape_pages
    from .swagger import detect_and_fetch_swagger

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    click.echo(f"🔍  Doelsite: {url}")

    # Stap 1: detecteer Swagger/OpenAPI — als gevonden, geen HTML scraping nodig
    swagger_result = await detect_and_fetch_swagger(url)
    if swagger_result:
        click.echo("✅  Swagger/OpenAPI spec gevonden — sla HTML scraping over.")
        export({"swagger": swagger_result}, output, out_path)
        return

    # Stap 2: maak login strategie aan
    login_strategy = create_login_strategy(login, username, password)

    # Stap 3: ontdek alle pagina's
    click.echo("📡  Pagina's ontdekken...")
    pages = await discover_pages(url)
    click.echo(f"   → {len(pages)} pagina's gevonden.")

    # Stap 4: scrape pagina's met Playwright
    click.echo("🌐  Pagina's laden en content extraheren...")
    raw_pages = await scrape_pages(pages, login_strategy)

    # Stap 5: clean content via Claude API
    click.echo(f"🤖  Content opschonen via Claude ({len(raw_pages)} pagina's)...")
    cleaned_pages = await clean_pages(raw_pages)

    # Stap 6: exporteer
    click.echo(f"💾  Opslaan als {output} in {out_path}...")
    export(cleaned_pages, output, out_path)
    click.echo("✅  Klaar!")


if __name__ == "__main__":
    cli()
