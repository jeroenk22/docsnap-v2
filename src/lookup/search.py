"""Zoeken in documentatie met de beste methode die een site aanbiedt.

Methoden, van snel en compleet naar universeel:
- index: een zoekindex die de site als bestand meelevert (MkDocs
  search_index.json, Sphinx searchindex.js, llms.txt)
- confluence / zendesk: de publieke zoek-API van het platform
- api: de JSON-zoek-API achter de zoekbalk, geleerd door mee te luisteren
  terwijl de zoekbalk gebruikt wordt (Algolia DocSearch, eigen API's)
- results_url: een zoekresultatenpagina zoals /search?q=...
- ui: de zoekbalk zelf gebruiken en de verschenen resultaatlinks lezen
- sitemap: woorden in de URL's van de sitemap (laatste redmiddel)

Welke methoden een site heeft, wordt bij init (of de eerste zoekactie)
herkend en in site.yaml onder search.methods vastgelegd.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import math
import re
import time
from collections import Counter
from urllib.parse import quote, quote_plus, urljoin, urlparse

from playwright.async_api import BrowserContext, Page

from .render import goto
from .site import Site, norm_url

INDEX_TTL = 24 * 3600  # zoekindexbestanden een dag hergebruiken

_TAGS = re.compile(r"@@@(?:end)?hl@@@|<[^>]+>")
_WORD = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+")


class SearchAuthError(Exception):
    """De zoekfunctie vraagt om een (nieuwe) login."""


def _clean(s: object) -> str:
    return " ".join(html.unescape(_TAGS.sub("", str(s or ""))).split())


def _terms(q: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(q) if len(w) > 1]


def jget(obj: object, path: str) -> object:
    """Genest veld via een pad met punten en lijstindexen: 'data.hits.0.url'."""
    cur = obj
    for part in [p for p in (path or "").split(".") if p]:
        if isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _hit(url: str, title: str = "", snippet: str = "", updated: str = "") -> dict:
    return {
        "url": url,
        "title": _clean(title)[:150],
        "snippet": _clean(snippet)[:240],
        "updated": updated,
    }


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _prefixes(url: str) -> list[str]:
    """Basismappen van diep naar ondiep: /docs/a/b.html -> /docs/a/, /docs/, /."""
    p = urlparse(url)
    parts = [s for s in p.path.split("/") if s]
    if parts and ("." in parts[-1] or not p.path.endswith("/")):
        parts = parts[:-1]  # laatste deel is een pagina, geen map
    return [
        f"{_origin(url)}/" + "".join(f"{s}/" for s in parts[:i])
        for i in range(len(parts), -1, -1)
    ]


async def _get_text(context: BrowserContext, url: str) -> str | None:
    try:
        r = await context.request.get(url, timeout=20_000)
    except Exception:  # noqa: BLE001
        return None
    if r.status in (401, 403):
        raise SearchAuthError(f"HTTP {r.status} op {url}")
    return await r.text() if r.ok else None


# ---------------------------------------------------------------------------
# Zoekindexbestanden (MkDocs, Sphinx, llms.txt)
# ---------------------------------------------------------------------------


def _parse_mkdocs(text: str, base: str) -> list[dict]:
    docs = json.loads(text).get("docs", [])
    return [
        {
            "url": urljoin(base, d.get("location", "")),
            "title": d.get("title", ""),
            "text": d.get("text", ""),
        }
        for d in docs
        if d.get("location") is not None
    ]


def _parse_sphinx(text: str, base: str) -> list[dict]:
    """searchindex.js: Search.setIndex({...}) met docnames, titles en terms."""
    data = json.loads(text[text.index("(") + 1 : text.rindex(")")])
    names, titles = data.get("docnames", []), data.get("titles", [])
    words: dict[int, list[str]] = {}
    for key in ("terms", "titleterms"):
        for term, ids in (data.get(key) or {}).items():
            for i in ids if isinstance(ids, list) else [ids]:
                words.setdefault(i, []).append(term)
    # dirhtml-builder: pagina's als map/; html-builder: pagina.html
    suffix = (
        "/"
        if data.get("filenames") and not str(data["filenames"][0]).endswith(".rst")
        else ".html"
    )
    return [
        {
            "url": urljoin(base, name + suffix),
            "title": titles[i] if i < len(titles) else name,
            "text": " ".join(words.get(i, [])),
        }
        for i, name in enumerate(names)
    ]


def _parse_llms(text: str, base: str) -> list[dict]:
    """llms.txt: regels als '- [Titel](url): beschrijving'."""
    out = []
    for m in re.finditer(
        r"^\s*[-*]\s*\[([^\]]+)\]\(([^)\s]+)\)\s*:?\s*(.*)$", text, re.M
    ):
        out.append(
            {"url": urljoin(base, m.group(2)), "title": m.group(1), "text": m.group(3)}
        )
    return out


INDEX_KINDS = {
    "mkdocs": ("search/search_index.json", _parse_mkdocs),
    "sphinx": ("searchindex.js", _parse_sphinx),
    "llms": ("llms.txt", _parse_llms),
}


def _score_docs(docs: list[dict], q: str, limit: int) -> list[dict]:
    """Rangschik indexdocumenten met BM25 (zoals zoekmachines), met extra gewicht voor de titel.

    Lange pagina's die alle termen vaak bevatten (changelogs) winnen zo niet van
    de pagina die er echt over gaat. Termen matchen ook op hun begin, omdat
    Sphinx stammen bewaart ('configur'). Bij 1-2 termen moeten ze allemaal
    voorkomen, bij meer termen mag er één ontbreken.
    """
    terms = _terms(q)
    if not terms or not docs:
        return []
    parsed = [
        (
            Counter(_WORD.findall(d["title"].lower())),
            Counter(_WORD.findall(d["text"].lower())),
        )
        for d in docs
    ]
    avg_len = sum(sum(t.values()) for _, t in parsed) / len(parsed) or 1.0
    match_cache: dict[tuple[str, str], bool] = {}

    def matches(term: str, word: str) -> bool:
        key = (term, word)
        if key not in match_cache:
            stem = term[:5] if len(term) > 5 else term
            match_cache[key] = word.startswith(stem) or (
                len(word) >= 4 and term.startswith(word)
            )
        return match_cache[key]

    def tf(term: str, counts: Counter) -> int:
        return sum(n for w, n in counts.items() if matches(term, w))

    freqs = [{t: (tf(t, title), tf(t, text)) for t in terms} for title, text in parsed]
    df = {t: sum(1 for f in freqs if f[t][0] or f[t][1]) for t in terms}
    idf = {t: math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5)) for t in terms}
    need = len(terms) if len(terms) <= 2 else len(terms) - 1
    k1, b = 1.2, 0.75

    best: dict[str, tuple[float, dict]] = {}
    for d, (_, text), f in zip(docs, parsed, freqs, strict=True):
        if sum(1 for t in terms if f[t][0] or f[t][1]) < need:
            continue
        length = sum(text.values())
        score = 0.0
        for t in terms:
            t_title, t_text = f[t]
            score += (
                idf[t]
                * (t_text * (k1 + 1))
                / (t_text + k1 * (1 - b + b * length / avg_len))
            )
            score += 2 * idf[t] * (t_title > 0)
        url = d["url"].split("#")[0]
        key = norm_url(url)
        if key not in best or score > best[key][0]:
            best[key] = (score, {**d, "url": url})
    ranked = sorted(best.values(), key=lambda x: -x[0])[:limit]
    return [_hit(d["url"], d["title"], d["text"][:240]) for _, d in ranked]


async def _load_index(site: Site, context: BrowserContext, kind: str) -> list[dict]:
    url = site.get(f"search.index.{kind}")
    cache = site.dir / "search" / f"{kind}.txt"
    if cache.exists() and time.time() - cache.stat().st_mtime < INDEX_TTL:
        text = cache.read_text(encoding="utf-8")
    else:
        text = await _get_text(context, url)
        if text is None:
            raise RuntimeError(f"zoekindex niet bereikbaar: {url}")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
    name, parse = INDEX_KINDS[kind]
    return parse(
        text, url.removesuffix(name)
    )  # paden zijn relatief aan de map van de site


async def detect_index(site: Site, context: BrowserContext) -> dict[str, str]:
    """Zoek zoekindexbestanden in de basismappen van de start-URL."""
    found: dict[str, str] = {}
    for kind, (name, parse) in INDEX_KINDS.items():
        for prefix in _prefixes(site.base_url):
            try:
                text = await _get_text(context, prefix + name)
            except SearchAuthError:  # 401/403 op een pad dat niet bestaat: geen index
                continue
            if not text:
                continue
            try:
                if parse(text, prefix):
                    found[kind] = prefix + name
                    break
            except (ValueError, KeyError, TypeError):
                continue
    return found


async def search_index(
    site: Site, context: BrowserContext, q: str, limit: int
) -> list[dict]:
    docs: list[dict] = []
    for kind in site.get("search.index", {}) or {}:
        docs += await _load_index(site, context, kind)
    return _score_docs(docs, q, limit)


# ---------------------------------------------------------------------------
# Platform-API's
# ---------------------------------------------------------------------------


def _confluence_space(url: str) -> str | None:
    m = re.search(r"/(?:display|spaces|space)/([A-Za-z0-9_~-]+)", urlparse(url).path)
    return m.group(1) if m else None


async def _confluence_results(
    site: Site, context: BrowserContext, q: str, limit: int
) -> list[tuple[dict, str]]:
    """Ruwe zoekresultaten van Confluence, elk met de URL zoals Confluence hem geeft."""
    api = site.get("search.confluence.api_base")
    cql = f'siteSearch ~ "{q}" AND type in (page, blogpost)'
    if space := site.get("search.confluence.space"):
        cql += f' AND space = "{space}"'
    text = await _get_text(
        context, f"{api}/rest/api/search?cql={quote(cql)}&limit={limit}"
    )
    if text is None:
        raise RuntimeError("Confluence-zoek-API gaf geen antwoord")
    data = json.loads(text)
    base = str(jget(data, "_links.base") or api).rstrip("/")
    out = []
    for it in data.get("results", []):
        rel = it.get("url") or jget(it, "content._links.webui") or ""
        out.append((it, rel if rel.startswith("http") else base + rel))
    return out


def _confluence_ids(it: dict, url: str) -> tuple[str, str]:
    """(space-sleutel, pagina-id) van een Confluence-resultaat."""
    page_id = str(jget(it, "content.id") or "")
    if not page_id and (m := re.search(r"/pages/(\d+)", url)):
        page_id = m.group(1)
    space = str(jget(it, "content.space.key") or "")
    if not space and (m := re.search(r"/spaces?/([^/]+)/", url)):
        space = m.group(1)
    return space, page_id


async def search_confluence(
    site: Site, context: BrowserContext, q: str, limit: int
) -> list[dict]:
    template = site.get("search.confluence.page_url")
    out = []
    for it, url in await _confluence_results(site, context, q, limit):
        space, page_id = _confluence_ids(it, url)
        if template and space and page_id:  # portaal voor Confluence: eigen URL-vorm
            url = template.replace("{space}", space).replace("{id}", page_id)
        out.append(
            _hit(
                url,
                it.get("title") or jget(it, "content.title"),
                it.get("excerpt"),
                it.get("lastModified") or "",
            )
        )
    return out


def portal_template(links: list[str], site: Site, spaces: set[str]) -> str | None:
    """Leer de URL-vorm van een portaal voor Confluence uit links op de site.

    Een link als https://support.x.nl/space/TMS/1307213836/Titel met een bekende
    space-sleutel en een pagina-id wordt https://support.x.nl/space/{space}/{id}.
    """
    for link in links:
        if not _same_site(link, site):
            continue
        parts = urlparse(link).path.split("/")
        for i, part in enumerate(parts):
            if part not in spaces:
                continue
            for j in range(i + 1, len(parts)):
                if re.fullmatch(r"\d{5,}", parts[j]):
                    path = "/".join([*parts[:i], "{space}", *parts[i + 1 : j], "{id}"])
                    return _origin(site.base_url) + path
    return None


async def search_zendesk(
    site: Site, context: BrowserContext, q: str, limit: int
) -> list[dict]:
    url = f"{_origin(site.base_url)}/api/v2/help_center/articles/search.json?query={quote_plus(q)}&per_page={limit}"
    if locale := site.get("search.zendesk.locale"):
        url += f"&locale={locale}"
    text = await _get_text(context, url)
    if text is None:
        raise RuntimeError("Zendesk-zoek-API gaf geen antwoord")
    return [
        _hit(
            a.get("html_url", ""),
            a.get("title"),
            a.get("snippet"),
            a.get("edited_at") or a.get("updated_at") or "",
        )
        for a in json.loads(text).get("results", [])
    ]


async def _find_portal_template(page: Page, site: Site, spaces: set[str]) -> str | None:
    """Zoek op de huidige pagina, en zo nodig op een space-pagina, naar portaallinks."""
    links = await page.evaluate(HREFS_JS)
    if template := portal_template(links, site, spaces):
        return template
    space_pages = [
        u
        for u in links
        if _same_site(u, site)
        and any(seg in spaces for seg in urlparse(u).path.split("/"))
    ]
    for u in space_pages[:2]:
        with contextlib.suppress(Exception):
            await goto(page, u)
            if template := portal_template(await page.evaluate(HREFS_JS), site, spaces):
                return template
    return None


async def detect_platform_api(site: Site, context: BrowserContext, page: Page) -> dict:
    """Probeer de zoek-API's van Confluence en Zendesk; geef werkende config terug."""
    found: dict = {}
    api_base = await page.evaluate(
        "() => document.querySelector('meta[name=ajs-base-url]')?.content || ''"
    )
    for base in dict.fromkeys(filter(None, [api_base, _origin(site.base_url)])):
        cfg = {"api_base": base.rstrip("/"), "space": _confluence_space(site.base_url)}
        trial = Site(site.name)
        trial.cfg = {**site.cfg, "search": {"confluence": cfg}}
        try:
            results = await _confluence_results(trial, context, "a", 10)
        except (RuntimeError, ValueError, SearchAuthError):
            continue
        if any(not _same_site(url, site) for _, url in results):
            # De API linkt naar het onderliggende Confluence (bv. x.atlassian.net),
            # niet naar het portaal waar de gebruiker zit: leer de portaal-URL's
            spaces = {_confluence_ids(it, url)[0] for it, url in results} - {""}
            template = await _find_portal_template(page, site, spaces)
            if not template:
                continue  # links onbruikbaar; dan liever de zoekbalk van het portaal
            cfg["page_url"] = template
        found["confluence"] = {k: v for k, v in cfg.items() if v}
        break
    if (
        re.search(r"/hc/[a-z-]+/", site.base_url)
        or "zendesk" in (await page.content())[:200_000].lower()
    ):
        locale = re.search(r"/hc/([a-z]{2}(?:-[a-z]{2})?)/", site.base_url)
        trial = Site(site.name)
        trial.cfg = {
            **site.cfg,
            "search": {"zendesk": {"locale": locale.group(1) if locale else None}},
        }
        with contextlib.suppress(RuntimeError, ValueError, SearchAuthError):
            await search_zendesk(trial, context, "help", 1)
            found["zendesk"] = {"locale": locale.group(1)} if locale else {}
    return found


# ---------------------------------------------------------------------------
# De zoekbalk: gebruiken, en de API erachter leren
# ---------------------------------------------------------------------------

SEARCH_INPUTS = [
    "input[type=search]",
    "[role=search] input",
    "input[name*=search i]",
    "input[id*=search i]",
    "input[placeholder*=zoek i]",
    "input[placeholder*=search i]",
    "input[aria-label*=search i]",
    "input[aria-label*=zoek i]",
    "input[name=q]",
    "input[name=query]",
]
# Knoppen die eerst een zoekveld openen (DocSearch, Ctrl+K-dialogen)
SEARCH_BUTTONS = [
    ".DocSearch-Button",
    "button[aria-label*=search i]",
    "button[aria-label*=zoek i]",
    "[role=button][aria-label*=search i]",
    "button[class*=search i]",
    "[class*=search i] button",
]

ALL_LINKS_JS = r"""
() => [...document.querySelectorAll('a[href]')]
  .filter(a => { const r = a.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
  .map(a => ({href: a.href, text: (a.innerText || '').replace(/\s+/g, ' ').trim(),
              ctx: ((a.closest('li, article') || a).innerText || '').replace(/\s+/g, ' ').trim().slice(0, 240)}))
  .filter(a => a.text.length > 2)
"""
HREFS_JS = (
    "() => [...document.querySelectorAll('a[href]')].map(a => a.href.split('#')[0])"
)


async def _first_visible(page: Page, selectors: list[str]) -> str | None:
    for sel in selectors:
        loc = page.locator(sel)
        with contextlib.suppress(Exception):
            for i in range(min(await loc.count(), 5)):
                if await loc.nth(i).is_visible():
                    return f"{sel} >> nth={i}"
    return None


async def _click(page: Page, selector: str) -> None:
    """Klik, ook als een cookiebanner de muis onderschept."""
    try:
        await page.locator(selector).click(timeout=3_000)
    except Exception:  # noqa: BLE001
        await page.locator(selector).dispatch_event("click")


async def _search_input(page: Page, how: str) -> str | None:
    """Zoekveld vinden: direct, na een zoekknop, of na een sneltoets (Ctrl+K, /)."""
    if how == "button":
        btn = await _first_visible(page, SEARCH_BUTTONS)
        if not btn:
            return None
        with contextlib.suppress(Exception):
            await _click(page, btn)
        await asyncio.sleep(0.7)
    elif how == "keys":
        for key in ("Control+k", "/"):
            await page.keyboard.press(key)
            await asyncio.sleep(0.7)
            if sel := await _first_visible(page, SEARCH_INPUTS):
                return sel
        return None
    return await _first_visible(page, SEARCH_INPUTS)


def _same_site(url: str, site: Site) -> bool:
    return urlparse(url).netloc == urlparse(site.base_url).netloc


def _result_links(
    links: list[dict], before: set[str], site: Site, q: str, limit: int
) -> list[dict]:
    """Links die er vóór het zoeken niet stonden, op dezelfde site, zonder zoek-/paginalinks."""
    out, seen = [], set()
    for a in links:
        url = a["href"].split("#")[0]
        key = norm_url(url)
        if (
            url in before
            or not url.startswith("http")
            or not _same_site(url, site)
            # zoek-, filter- of paginalinks (?q=term&page=2); de term mag wel in het pad
            or any(v in urlparse(url).query for v in (quote(q), quote_plus(q)))
            or key in seen
        ):
            continue
        seen.add(key)
        out.append(_hit(url, a["text"], a["ctx"]))
    return out[:limit]


async def _type_and_collect(
    page: Page, sel: str, q: str, site: Site, before: set[str], limit: int
) -> list[dict]:
    """Typ (zonder muisklik: banners kunnen die onderscheppen) en lees de resultaten."""
    box = page.locator(sel)
    await box.focus()
    await box.fill("")
    # Dropdowns tonen bij focus vaak al 'recent bekeken' of 'populair': geen resultaten
    await asyncio.sleep(0.8)
    before |= set(await page.evaluate(HREFS_JS))
    await box.press_sequentially(
        q, delay=40
    )  # sommige velden reageren alleen op toetsen
    await asyncio.sleep(1.5)  # live-resultaten
    hits = _result_links(await page.evaluate(ALL_LINKS_JS), before, site, q, limit)
    if not hits:  # geen live-resultaten: echte zoekopdracht
        await box.press("Enter")
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=6_000)
        await asyncio.sleep(1.5)
        hits = _result_links(await page.evaluate(ALL_LINKS_JS), before, site, q, limit)
    return hits


def _mentions(captured: dict, q: str) -> bool:
    """Bevat dit verzoek de zoekterm in de query of body (niet in het pad van een bestand)?"""
    variants = (q, quote(q), quote_plus(q))
    query = urlparse(captured["url"]).query
    return any(v in query or v in (captured.get("body") or "") for v in variants)


async def use_search_box(
    site: Site, context: BrowserContext, q: str, limit: int, capture: list | None = None
) -> tuple[list[dict], str]:
    """Gebruik de zoekbalk zoals een mens en lees de resultaatlinks.

    Probeert achtereenvolgens het zichtbare zoekveld, een zoekknop en de
    sneltoetsen. capture: lijst waarin JSON-responses van XHR/fetch komen (om
    de API te leren). Returns (hits, url van de pagina na het zoeken).
    """
    page = await context.new_page()

    async def on_response(resp) -> None:
        if resp.request.resource_type not in ("xhr", "fetch"):
            return
        if "json" not in (resp.headers.get("content-type") or ""):
            return
        with contextlib.suppress(Exception):
            capture.append(
                {
                    "url": resp.url,
                    "method": resp.request.method,
                    "body": resp.request.post_data,
                    "headers": resp.request.headers,
                    "json": await resp.json(),
                }
            )

    if capture is not None:
        page.on("response", on_response)
    start = site.get("search.ui.page") or site.base_url
    found_input = False
    try:
        for how in ("direct", "button", "keys"):
            await goto(page, start)
            before = set(
                await page.evaluate(HREFS_JS)
            )  # wat er al stond, is geen resultaat
            sel = await _search_input(page, how)
            if not sel:
                continue
            found_input = True
            if capture is not None:
                capture.clear()  # alleen verkeer dat door het zoeken ontstaat
            try:
                hits = await _type_and_collect(page, sel, q, site, before, limit)
            except Exception:  # noqa: BLE001  (veld verdween, onbruikbaar, ...)
                continue
            searched = page.url.split("#")[0] != start.split("#")[0]
            # Gelukt: resultaatlinks, een resultatenpagina, of een response met een
            # resultatenlijst (niet: telemetrie die de getypte tekst meestuurt)
            api_like = capture is not None and learn_api(capture, q, site) is not None
            if hits or searched or api_like:
                return hits, page.url
        if not found_input:
            raise RuntimeError("geen zoekbalk gevonden")
        return [], page.url
    finally:
        await page.close()


async def search_ui(
    site: Site, context: BrowserContext, q: str, limit: int
) -> list[dict]:
    hits, _ = await use_search_box(site, context, q, limit)
    return hits


URL_KEYS = (
    "url",
    "html_url",
    "href",
    "link",
    "path",
    "location",
    "permalink",
    "webui",
    "slug",
    "uri",
)
# Volgorde = voorkeur; lvl1/lvl0 zijn de paginatitel en sectie bij Algolia DocSearch
TITLE_KEYS = (
    "title",
    "pagetitle",
    "page_title",
    "name",
    "heading",
    "subject",
    "label",
    "lvl1",
    "lvl0",
)
SNIPPET_KEYS = (
    "excerpt",
    "snippet",
    "summary",
    "description",
    "content",
    "body",
    "text",
    "highlight",
)
DATE_KEYS = (
    "updated_at",
    "edited_at",
    "lastmodified",
    "last_modified",
    "modified",
    "updated",
    "lastupdated",
    "modified_at",
    "date",
    "lastmodifieddate",
)


def _find_lists(obj: object, path: str = ""):
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _find_lists(v, f"{path}.{k}" if path else k)
    if isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            if isinstance(v, dict | list):
                yield from _find_lists(v, f"{path}.{i}" if path else str(i))


def _find_key(
    item: dict, wanted: tuple, depth: int = 0, prefix: str = ""
) -> str | None:
    """Pad naar het eerste gevraagde veld (in volgorde van `wanted`) met een tekstwaarde.

    Zoekt ook in geneste objecten, maar niet in velden met een _ ervoor (zoals
    Algolia's _highlightResult: kopieën met opmaak).
    """
    lowered = {k.lower().replace("-", "_"): k for k in item}
    for w in wanted:
        k = lowered.get(w)
        if k is not None and isinstance(item[k], str | int) and str(item[k]):
            return prefix + k
    if depth < 2:
        for k, v in item.items():
            if (
                isinstance(v, dict)
                and not k.startswith("_")
                and (found := _find_key(v, wanted, depth + 1, prefix + k + "."))
            ):
                return found
    return None


def _template(s: str, term: str, raw_first: bool = False) -> str:
    """Vervang de zoekterm door een plaatshouder: {q} gecodeerd, {q+} met +, {qraw} letterlijk.

    raw_first voor een JSON-body: een zoekterm van één woord ziet er gecodeerd en
    letterlijk hetzelfde uit, maar in een body hoort hij letterlijk.
    """
    options = [(quote(term), "{q}"), (quote_plus(term), "{q+}"), (term, "{qraw}")]
    for value, holder in options[::-1] if raw_first else options:
        if value and value in s:
            return s.replace(value, holder)
    return s


def learn_api(captured: list[dict], term: str, site: Site) -> dict | None:
    """Kies uit het netwerkverkeer de JSON-response die op zoekresultaten lijkt."""
    best, best_score = None, 0
    for c in captured:
        if not _mentions(c, term):
            continue
        for path, items in _find_lists(c["json"]):
            url_key = _find_key(items[0], URL_KEYS)
            if not url_key:
                continue
            title_key = _find_key(items[0], TITLE_KEYS)
            score = len(items) + (5 if title_key else 0)
            if score <= best_score:
                continue
            headers = {
                k: v
                for k, v in (c.get("headers") or {}).items()
                if k.lower().startswith("x-") or k.lower() in ("content-type", "accept")
            }
            best_score = score
            best = {
                "method": c["method"],
                "url": _template(c["url"], term),
                "body": _template(c.get("body") or "", term, raw_first=True) or None,
                "headers": headers or None,
                "results_path": path,
                "url_field": url_key,
                "title_field": title_key or url_key,
                "snippet_field": _find_key(items[0], SNIPPET_KEYS) or "",
                "updated_field": _find_key(items[0], DATE_KEYS) or "",
                "url_prefix": _origin(site.base_url),
            }
    return best


def _fill(template: str, q: str) -> str:
    return (
        template.replace("{q}", quote(q))
        .replace("{q+}", quote_plus(q))
        .replace("{qraw}", json.dumps(q)[1:-1])
    )


async def search_api(
    site: Site, context: BrowserContext, q: str, limit: int
) -> list[dict]:
    cfg = site.get("search.api") or {}
    url = _fill(cfg["url"], q)
    headers = cfg.get("headers") or {}
    if cfg.get("method", "GET").upper() == "POST":
        r = await context.request.post(
            url, data=_fill(cfg.get("body") or "", q), headers=headers, timeout=20_000
        )
    else:
        r = await context.request.get(url, headers=headers, timeout=20_000)
    if r.status in (401, 403):
        raise SearchAuthError(f"zoek-API HTTP {r.status}")
    if not r.ok:
        raise RuntimeError(f"zoek-API HTTP {r.status}")
    items = jget(await r.json(), cfg.get("results_path", "")) or []

    def field(it: dict, name: str) -> str:
        path = cfg.get(
            name
        )  # leeg pad = veld bestaat niet (jget zou het hele item geven)
        return str(jget(it, path) or "") if path else ""

    out, seen = [], set()
    for it in items:
        u = field(it, "url_field")
        if not u:
            continue
        url = urljoin(cfg.get("url_prefix", "") + "/", u).split("#")[0]
        if norm_url(url) in seen:  # meerdere secties van dezelfde pagina
            continue
        seen.add(norm_url(url))
        out.append(
            _hit(
                url,
                field(it, "title_field"),
                field(it, "snippet_field"),
                field(it, "updated_field"),
            )
        )
    return out[:limit]


async def search_results_url(
    site: Site, context: BrowserContext, q: str, limit: int
) -> list[dict]:
    """Laad de zoekresultatenpagina; resultaten zijn links die niet op de startpagina staan."""
    page = await context.new_page()
    try:
        await goto(page, site.base_url)
        before = set(await page.evaluate(HREFS_JS))
        await goto(page, _fill(site.get("search.results_url"), q))
        await asyncio.sleep(1.0)
        return _result_links(await page.evaluate(ALL_LINKS_JS), before, site, q, limit)
    finally:
        await page.close()


async def probe_search_box(site: Site, context: BrowserContext, term: str) -> dict:
    """Gebruik de zoekbalk met een proefterm en leer hoe de site zoekt.

    Returns search-config: {'api': ...} als de API zonder zoekbalk herhaald kan
    worden, anders {'results_url': ...} of {'ui': {...}}; {} als er geen
    werkende zoekbalk is.
    """
    captured: list[dict] = []
    try:
        hits, url_after = await use_search_box(
            site, context, term, 10, capture=captured
        )
    except Exception:  # noqa: BLE001  (herkenning mag init nooit laten mislukken)
        return {}

    api = learn_api(captured, term, site)
    if api:
        trial = Site(site.name)
        trial.cfg = {**site.cfg, "search": {"api": api}}
        with contextlib.suppress(Exception):
            real = [h["url"] for h in await search_api(trial, context, term, 10)]
            # Een echte zoek-API geeft voor onzin iets anders (meestal niets)
            nonsense = [
                h["url"] for h in await search_api(trial, context, NONSENSE, 10)
            ]
            if [u for u in real if _same_site(u, site)] and set(real) != set(nonsense):
                return {"api": api}

    if any(v in url_after for v in (quote(term), quote_plus(term), term)):
        template = _template(url_after, term)
        trial = Site(site.name)
        trial.cfg = {**site.cfg, "search": {"results_url": template}}
        with contextlib.suppress(Exception):
            if await search_results_url(trial, context, term, 10):
                return {"results_url": template}

    return {"ui": {"page": site.base_url}} if hits else {}


# ---------------------------------------------------------------------------
# Sitemap (laatste redmiddel)
# ---------------------------------------------------------------------------


async def search_sitemap(
    site: Site, context: BrowserContext, q: str, limit: int
) -> list[dict]:
    urls: list[str] = []
    for cand in dict.fromkeys(p + "sitemap.xml" for p in _prefixes(site.base_url)):
        text = await _get_text(context, cand)
        if text and "<loc>" in text:
            urls = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", text)
            break
    docs = [
        {
            "url": u,
            "title": urlparse(u).path.rstrip("/").rsplit("/", 1)[-1].replace("-", " "),
            "text": urlparse(u).path.replace("/", " ").replace("-", " "),
        }
        for u in urls
    ]
    return _score_docs(docs, q, limit)


# ---------------------------------------------------------------------------
# Herkennen en uitvoeren
# ---------------------------------------------------------------------------

METHODS = {
    "index": search_index,
    "confluence": search_confluence,
    "zendesk": search_zendesk,
    "api": search_api,
    "results_url": search_results_url,
    "ui": search_ui,
    "sitemap": search_sitemap,
}

METHOD_LABELS = {
    "index": "zoekindex van de site",
    "confluence": "Confluence-zoek-API",
    "zendesk": "Zendesk-zoek-API",
    "api": "zoek-API achter de zoekbalk",
    "results_url": "zoekresultatenpagina",
    "ui": "zoekbalk",
    "sitemap": "sitemap",
}


NONSENSE = "qxzvjkwpt"  # proefterm die op geen enkele site resultaten hoort te geven


def probe_term(title: str) -> str:
    """Proefterm uit de paginatitel: het langste woord van minstens 5 letters."""
    words = sorted(
        (w for w in _WORD.findall(title) if len(w) >= 5 and not w.isdigit()),
        key=len,
        reverse=True,
    )
    return words[0].lower() if words else "help"


async def detect_search(
    site: Site, context: BrowserContext, page: Page, title: str
) -> dict:
    """Bepaal welke zoekmethoden de site ondersteunt; geeft de search-config terug."""
    search: dict = {}
    if index := await detect_index(site, context):
        search["index"] = index
    search.update(await detect_platform_api(site, context, page))
    search.update(await probe_search_box(site, context, probe_term(title)))
    order = ["index", "confluence", "zendesk", "api", "results_url", "ui"]
    search["methods"] = [m for m in order if m in search] + ["sitemap"]
    return search


def section_bonus(url: str, base_url: str) -> float:
    """Extra gewicht voor resultaten in dezelfde sectie als de startpagina.

    Grote sites (Microsoft Learn, Confluence met meerdere spaces) doorzoeken
    alles; wat onder hetzelfde pad valt is meestal relevanter. 0,25 per gedeeld
    padsegment, maximaal 3.
    """
    a = [p for p in urlparse(url).path.lower().split("/") if p]
    b = [p for p in urlparse(base_url).path.lower().split("/") if p][
        :-1
    ]  # zonder de pagina zelf
    shared = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        shared += 1
    return 0.25 * min(shared, 3)


async def run_search(
    site: Site, context: BrowserContext, queries: list[str], limit: int, report
) -> list[dict]:
    """Voer alle zoekvragen uit en voeg de resultaten samen (vaker gevonden = hoger).

    Per zoekvraag worden de methoden in volgorde gebruikt tot er `limit`
    resultaten zijn; de trage zoekbalk en de sitemap alleen als er bijna niets is.
    """
    methods = site.get("search.methods") or ["sitemap"]
    merged: dict[str, dict] = {}
    for q in queries:
        hits: list[dict] = []
        used = []
        for name in methods:
            if len(hits) >= limit or (name in ("ui", "sitemap") and len(hits) >= 3):
                break
            t0 = time.monotonic()
            try:
                found = await METHODS[name](site, context, q, limit)
            except SearchAuthError:
                raise
            except Exception as e:  # noqa: BLE001
                report(f"  ! '{q}' via {METHOD_LABELS[name]} mislukt: {str(e)[:120]}")
                continue
            known = {norm_url(h["url"]) for h in hits}
            new = [h for h in found if norm_url(h["url"]) not in known]
            if new:
                hits += new
                used.append(f"{METHOD_LABELS[name]} ({time.monotonic() - t0:.1f}s)")
        report(f'  - "{q}": {len(hits)} resultaten via {" + ".join(used) or "-"}')
        for rank, h in enumerate(hits[:limit]):
            key = norm_url(h["url"])
            score = 1.0 - rank / max(limit, 1)
            if key in merged:
                merged[key]["score"] += score
                merged[key]["queries"].append(q)
                merged[key]["updated"] = merged[key]["updated"] or h["updated"]
            else:
                bonus = section_bonus(h["url"], site.base_url)
                merged[key] = {**h, "score": score + bonus, "queries": [q]}
    return sorted(merged.values(), key=lambda h: -h["score"])
