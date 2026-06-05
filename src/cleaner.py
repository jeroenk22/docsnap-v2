"""
Reinig HTML naar Markdown via Claude API.

Gebruikt claude-haiku-4-5-20251001 (goedkoop, snel) om per pagina:
- Navigatie, headers, footers en cookiebanners te verwijderen
- Alleen de documentatie-inhoud te extraheren
- De content te converteren naar schone Markdown

Voor het aanroepen worden scripts, stijlen en binaire data uit de HTML
gestript zodat het token-budget volledig naar documentatie-inhoud gaat.
"""
from __future__ import annotations

import asyncio
import os
import re

import anthropic

from .scraper import ScrapedPage

MODEL = "claude-haiku-4-5-20251001"
MAX_HTML_CHARS = 100_000  # na pre-cleaning; body-only HTML is veel compacter
MAX_RESPONSE_TOKENS = 8_192  # ruim genoeg voor pagina's met lange code blocks

SYSTEM_PROMPT = """Je bent een HTML-naar-Markdown converter gespecialiseerd in documentatiesites.

Taak: Extraheer ALLEEN de documentatie-inhoud uit de gegeven HTML en converteer naar schone Markdown.

Regels:
- Verwijder: navigatiemenus, sidebars, headers, footers, cookiebanners, advertenties, breadcrumbs
- Bewaar: alle technische inhoud, code blocks, tabellen, afbeeldingen (als alt-tekst), links
- Code blocks: geef deze ALTIJD volledig en onafgekapt weer — nooit afkorten met "..." of "[rest van code]"
- Converteer: koppen naar # ## ###, code naar ``` blocks met de juiste taal, lijsten naar - of 1.
- Bewaar de hiërarchische structuur van de documentatie
- Geef ALLEEN de Markdown terug, geen uitleg of toelichting
- Als er geen documentatie-inhoud is (bijv. loginpagina), geef dan een lege string terug"""


def _preprocess_html(html: str) -> str:
    """Strip niet-inhoudelijke HTML voor efficiënter token-gebruik.

    Verwijdert scripts, stijlen, SVGs en base64-data. De documentatie-inhoud
    (tekst, koppen, code blocks, tabellen) blijft intact.
    """
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<svg\b[^>]*>.*?</svg>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'data:[^"\';\s]+;base64,[A-Za-z0-9+/=]+', "", html)
    return html


async def clean_pages(pages: list[ScrapedPage], concurrency: int = 3) -> list[dict]:
    """Reinig een lijst van HTML pagina's naar Markdown via Claude.

    Args:
        pages:       Gescrapede pagina's met HTML.
        concurrency: Maximaal aantal gelijktijdige Claude API calls.
                     Standaard 3 om binnen de Haiku rate limit te blijven.

    Returns:
        Lijst van dicts met url, title en markdown.
    """
    semaphore = asyncio.Semaphore(concurrency)
    # max_retries=6: SDK voert exponentiële backoff uit bij 429 (rate limit)
    # en 529 (overloaded) — tot ~64 seconden wachttijd per poging.
    client = anthropic.AsyncAnthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        max_retries=6,
    )

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
        html_preprocessed = _preprocess_html(page.html)
        html_truncated = html_preprocessed[:MAX_HTML_CHARS]

        # De systeemprompt is identiek voor elke pagina — cache hem zodat die
        # tokens na de eerste call niet meer tellen voor de rate limit.
        message = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_RESPONSE_TOKENS,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
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
