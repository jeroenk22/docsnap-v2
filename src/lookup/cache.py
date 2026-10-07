"""Opgehaalde pagina's bewaren als Markdown, met een index per bron.

Versiecontrole (is de pagina sinds de vorige keer gewijzigd?) en diffs volgen
later; nu wordt per pagina alleen vastgelegd of de inhoud nieuw, gelijk of
gewijzigd is ten opzichte van de vorige keer.
"""

from __future__ import annotations

import re
from pathlib import Path

from .site import Site, norm_url, now_iso, sha
from .usage import text_tokens


def entry(site: Site, url: str) -> dict | None:
    return site.index.get(norm_url(url))


def page_path(site: Site, slug: str) -> Path:
    return site.pages_dir / f"{slug}.md"


def _content_hash(markdown: str) -> str:
    """Hash op inhoud: whitespace en afbeeldingspaden tellen niet mee."""
    t = re.sub(r"\]\([^)]*images/[^)]*\)", "](img)", markdown)
    return sha(re.sub(r"\s+", " ", t).strip())


def store_page(site: Site, url: str, title: str, md: str, meta: dict) -> str:
    """Schrijf de pagina en werk de index bij.

    De kop bevat de bron-URL; daar verwijzen citaten later naar.
    Returns 'nieuw', 'ongewijzigd' of 'gewijzigd'.
    """
    key = norm_url(url)
    old = site.index.get(key)
    slug = old["slug"] if old else meta["slug"]
    path = page_path(site, slug)
    new_hash = _content_hash(md)

    body = md.lstrip()
    first, _, rest = body.partition("\n")
    if first.startswith("# ") and first[2:].strip() == title.strip():
        body = rest  # titel staat al in de kop
    text = f"<!-- doc-lookup | bron: {url} | opgehaald: {now_iso()} -->\n# {title}\n\nBron: {url}\n\n{body}"

    if not old or not path.exists():
        change = "nieuw"
    elif old.get("hash") == new_hash:
        change = "ongewijzigd"
    else:
        change = "gewijzigd"
    if change != "ongewijzigd":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    site.index[key] = {
        "url": url,
        "slug": slug,
        "title": title,
        "hash": new_hash,
        "fetched_at": now_iso()
        if change != "ongewijzigd"
        else old.get("fetched_at", now_iso()),
        "checked_at": now_iso(),
        "headings": re.findall(r"^#{1,4}\s+(.+)$", md, re.M)[:40],
        "tokens": text_tokens(path.read_text(encoding="utf-8")),
        **{k: v for k, v in meta.items() if k != "slug"},
    }
    return change
