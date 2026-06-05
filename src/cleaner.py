"""
Reinig HTML naar Markdown via Claude API.

Gebruikt claude-haiku-4-5-20251001 (goedkoop, snel) om per pagina:
- Navigatie, headers, footers en cookiebanners te verwijderen
- Alleen de documentatie-inhoud te extraheren
- De content te converteren naar schone Markdown
"""
from __future__ import annotations

import asyncio
import os

import anthropic

from .scraper import ScrapedPage

MODEL = "claude-haiku-4-5-20251001"
MAX_HTML_CHARS = 50_000  # Begrens HTML om tokens te besparen

SYSTEM_PROMPT = """Je bent een HTML-naar-Markdown converter gespecialiseerd in documentatiesites.

Taak: Extraheer ALLEEN de documentatie-inhoud uit de gegeven HTML en converteer naar schone Markdown.

Regels:
- Verwijder: navigatiemenus, sidebars, headers, footers, cookiebanners, advertenties, breadcrumbs
- Bewaar: alle technische inhoud, code blocks, tabellen, afbeeldingen (als alt-tekst), links
- Converteer: koppen naar # ## ###, code naar ``` blocks, lijsten naar - of 1.
- Bewaar de hiërarchische structuur van de documentatie
- Geef ALLEEN de Markdown terug, geen uitleg of toelichting
- Als er geen documentatie-inhoud is (bijv. loginpagina), geef dan een lege string terug"""


async def clean_pages(pages: list[ScrapedPage], concurrency: int = 5) -> list[dict]:
    """Reinig een lijst van HTML pagina's naar Markdown via Claude.

    Args:
        pages:       Gescrapede pagina's met HTML.
        concurrency: Maximaal aantal gelijktijdige Claude API calls.

    Returns:
        Lijst van dicts met url, title en markdown.
    """
    semaphore = asyncio.Semaphore(concurrency)
    client = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    tasks = [_clean_single_page(client, semaphore, page) for page in pages]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    cleaned = []
    for page, result in zip(pages, results, strict=True):
        if isinstance(result, Exception):
            print(f"⚠️  Fout bij reinigen van {page.url}: {result}")
            cleaned.append({"url": page.url, "title": page.title, "markdown": ""})
        else:
            cleaned.append(result)

    return cleaned


async def _clean_single_page(
    client: anthropic.AsyncAnthropic,
    semaphore: asyncio.Semaphore,
    page: ScrapedPage,
) -> dict:
    """Reinig één pagina via Claude API."""
    async with semaphore:
        html_truncated = page.html[:MAX_HTML_CHARS]

        message = await client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"URL: {page.url}\n"
                        f"Titel: {page.title}\n\n"
                        f"HTML:\n{html_truncated}"
                    ),
                }
            ],
        )

        markdown = message.content[0].text if message.content else ""
        return {"url": page.url, "title": page.title, "markdown": markdown}
