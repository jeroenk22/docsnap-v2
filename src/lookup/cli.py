"""docsnap-lookup: documentatie opzoeken op aanvraag, voor gebruik door Claude Code.

Elk commando print korte regels die Claude aan de gebruiker kan doorgeven en
sluit af met een [tokens]-regel. Exitcodes: 3 = login nodig, 4 = structuur van
de site gewijzigd, 5 = pagina bleef leeg.
"""

from __future__ import annotations

import asyncio
import functools
import json
import re
import sys
import time
from collections.abc import Callable

import click

from . import cache
from .convert import completeness, html_to_markdown, is_icon
from .render import (
    CANDIDATES_JS,
    DEFAULT_CONTENT_SELECTORS,
    PLATFORM_DEFAULTS,
    choose_container,
    detect_platform,
    download_images,
    goto,
    is_positional,
    render_page,
    screenshot,
    wait_until_stable,
)
from .search import METHOD_LABELS, SearchAuthError, detect_search, run_search
from .session import (
    BrowserUnavailable,
    is_auth_wall,
    login_interactive,
    login_with_form,
    open_context,
)
from .site import (
    Site,
    SiteNotFound,
    default_name,
    list_sites,
    load_env_file,
    norm_url,
    now_iso,
    url_slug,
)
from .usage import RUN, fmt_tokens, say, session_line, warn

EXIT_AUTH = 3
EXIT_STRUCTURE = 4
EXIT_EMPTY = 5


def _command(fn: Callable) -> Callable:
    """Draai een async commando, vang bekende fouten af en print de [tokens]-regel."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs) -> None:
        session_id = click.get_current_context().obj.get("session")
        try:
            code = asyncio.run(fn(*args, **kwargs)) or 0
        except SiteNotFound as e:
            say(f"FOUT: {e}")
            code = 2
        except BrowserUnavailable as e:
            say(
                f"FOUT: browser start niet ({e}). Installeer eenmalig: playwright install chromium"
            )
            code = 1
        click.echo(session_line(session_id))
        sys.exit(code)

    return wrapper


@click.group()
@click.option(
    "--session",
    "session_id",
    default=None,
    help="Claude Code-sessie-ID; telt het tokenverbruik per chat op.",
)
@click.pass_context
def cli(ctx: click.Context, session_id: str | None) -> None:
    """Zoek en lees documentatie (openbaar of achter een login) voor Claude Code."""
    # Windows-console (cp1252) mag nooit crashen op tekens uit documentatie
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ctx.obj = {"session": session_id}


# ---------------------------------------------------------------------------
# sites
# ---------------------------------------------------------------------------


@cli.command()
@_command
async def sites() -> int:
    """Toon de bekende documentatiebronnen."""
    names = list_sites()
    if not names:
        say("Nog geen documentatiebronnen. Maak er een aan met: init <url>")
    for name in names:
        s = Site.load(name)
        if s.get("login.mode", "none") == "none":
            auth = "geen login nodig"
        else:
            auth = "sessie opgeslagen" if s.has_auth() else "NIET ingelogd"
        say(f"- {name}: {s.base_url}")
        say(
            f"    platform: {s.get('platform')} | {len(s.index)} pagina's in cache | {auth}"
        )
    return 0


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("url")
@click.option("--name", help="Korte naam voor de bron (standaard afgeleid van de URL).")
@click.option("--refresh", is_flag=True, help="Herken de structuur opnieuw.")
@_command
async def init(url: str, name: str | None, refresh: bool) -> int:
    """Leg een nieuwe bron vast en herken login, platform en content-container."""
    t0 = time.monotonic()
    name = name or default_name(url)
    site = Site(name)
    if site.cfg_path.exists():
        site = Site.load(name)
        if not refresh:
            say(
                f"Bron '{name}' bestaat al ({site.base_url}). Gebruik --refresh om opnieuw te herkennen."
            )
            return 0
        site.cfg.setdefault("content", {}).pop("selector", None)
    else:
        site.cfg = {"name": name, "base_url": url, "created_at": now_iso()}
    site.ensure_dirs()

    say(f"[1/5] Pagina openen: {url}")
    async with open_context(site) as context:
        page = await context.new_page()
        resp = await goto(page, url)
        say("[2/5] Login-muur controleren")
        # Eerst de eenduidige signalen, zodat een loginpagina niet 12s wacht
        wall = await is_auth_wall(page, resp, site, root_found=True)
        if not wall:
            _, sel, chars = await wait_until_stable(page, site, timeout=12)
            wall = await is_auth_wall(page, resp, site, bool(sel and chars > 200))
        if wall:
            mode = site.get("login.mode", "none")
            login_cfg = site.cfg["login"] = {
                **(site.get("login") or {}),
                "mode": mode if mode != "none" else "manual",
            }
            if norm_url(page.url) != norm_url(url):  # doorgestuurd naar een loginpagina
                login_cfg["login_url"] = page.url
            site.cfg.setdefault("platform", "generic")
            site.save_cfg()
            await screenshot(page, site.debug_dir / "init-login.png")
            say(f"  Login vereist ({wall}). Pagina: {page.url}")
            say(
                f"  Volgende stap: login {name}  (daarna: init {url} --name {name} --refresh)"
            )
            return EXIT_AUTH
        site.cfg.setdefault("login", {"mode": "none"})
        say("  ingelogd" if site.has_auth() else "  geen login nodig")

        say("[3/5] Platform en content-container herkennen")
        platform = detect_platform(await page.content(), page.url)
        site.cfg["platform"] = platform
        cands = await page.evaluate(CANDIDATES_JS, DEFAULT_CONTENT_SELECTORS)
        chosen = choose_container(cands)
        content = site.cfg.setdefault("content", {})
        if chosen:
            content["selector"] = chosen["selector"]
        defaults = PLATFORM_DEFAULTS.get(platform, {})
        content.setdefault("title_selector", defaults.get("title_selector", ""))
        content.setdefault("remove", defaults.get("remove", []))
        content.setdefault("min_chars", 150)
        say(f"  platform: {platform}")
        say("  content-kandidaten (selector | tekens | link-ratio | koppen | begin):")
        for c in cands:
            mark = "->" if chosen and c["selector"] == chosen["selector"] else "  "
            say(
                f"   {mark} {c['selector']} | {c['chars']} | {c['link_ratio']} | "
                f"{c['headings']} | {c['preview'][:70]}"
            )

        if chosen and is_positional(chosen["selector"]):
            warn(
                f"selector '{chosen['selector']}' hangt af van de positie op de pagina en kan op "
                "andere pagina's naar het verkeerde blok wijzen; laat de structuur controleren"
            )

        shot = await screenshot(page, site.debug_dir / "init.png")
        say("[4/5] Zoekmogelijkheden herkennen")
        site.cfg["search"] = await detect_search(
            site, context, page, await page.title()
        )
        _say_search(site)

        say("[5/5] Opslaan")
        site.cfg["structure"] = {"verified_at": now_iso(), "sample_url": url}
        site.save_cfg()
    say(f"Klaar in {time.monotonic() - t0:.1f}s. Config: {site.cfg_path}")
    if shot:
        say(f"Screenshot ter controle: {shot}")
    return 0


def _say_search(site: Site) -> None:
    labels = [METHOD_LABELS[m] for m in site.get("search.methods", [])]
    say(f"  zoeken via: {', '.join(labels)}")
    if site.get("search.methods", []) == ["sitemap"]:
        warn(
            "geen zoekfunctie herkend; zoeken gaat alleen op woorden in de URL's van de "
            "sitemap. Kies pagina's liever via de inhoudsopgave."
        )


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("site_name", metavar="SITE")
@click.argument("queries", nargs=-1, required=True)
@click.option(
    "--limit", default=10, show_default=True, help="Max. resultaten per zoekvraag."
)
@_command
async def search(site_name: str, queries: tuple[str, ...], limit: int) -> int:
    """Zoek met de zoekfunctie van de site; geef 3-6 varianten (NL/EN, synoniemen)."""
    site = Site.load(site_name)
    t0 = time.monotonic()
    async with open_context(site) as context:
        if not site.get("search.methods"):  # bron van voor de zoekfunctie
            say("[0/2] Zoekmogelijkheden herkennen (eenmalig)")
            page = await context.new_page()
            await goto(page, site.get("structure.sample_url") or site.base_url)
            site.cfg["search"] = await detect_search(
                site, context, page, await page.title()
            )
            await page.close()
            site.save_cfg()
            _say_search(site)

        say(f"[1/2] Zoeken in {site.name} ({len(queries)} zoekvraag/-vragen)")
        try:
            results = await run_search(site, context, list(queries), limit, say)
        except SearchAuthError as e:
            say(
                f"FOUT: login vereist of sessie verlopen ({e}). Voer uit: login {site.name}"
            )
            return EXIT_AUTH
    results = results[: limit * 2]

    say(f"[2/2] {len(results)} unieke resultaten ({time.monotonic() - t0:.1f}s)")
    for i, h in enumerate(results, 1):
        e = cache.entry(site, h["url"])
        status = f"in cache, {fmt_tokens(e['tokens'])}" if e else "nog niet opgehaald"
        upd = f" | bijgewerkt {h['updated'][:10]}" if h.get("updated") else ""
        say(f" {i}. {h['title'] or h['url']}{upd}")
        say(f"    {h['url']}")
        say(f"    {status} | gevonden met: {', '.join(h['queries'])}")
        if h.get("snippet"):
            say(f'    "{h["snippet"][:200]}"')
    if results:
        say(f"Ophalen: fetch {site.name} <nummers>, bv. fetch {site.name} 1 2 4-6")
    else:
        say(
            "Niets gevonden. Probeer andere termen (synoniemen, Engels/Nederlands, vakjargon)."
        )
    site.last_search_path.write_text(
        json.dumps(
            {"ts": now_iso(), "queries": list(queries), "results": results},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    site.log(
        site.usage_path,
        {"cmd": "search", "queries": list(queries), "results": len(results)},
    )
    return 0


def _targets(site: Site, items: tuple[str, ...]) -> list[str]:
    """URL's uit losse URL's en nummers/reeksen uit de laatste zoekactie ('1', '4-6')."""
    last = []
    if site.last_search_path.exists():
        last = json.loads(site.last_search_path.read_text(encoding="utf-8")).get(
            "results", []
        )
    urls: list[str] = []
    for it in items:
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", it)
        if not m:
            urls.append(it)
            continue
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        for n in range(a, b + 1):
            if not 1 <= n <= len(last):
                raise click.UsageError(
                    f"Resultaat {n} bestaat niet; de laatste zoekactie had {len(last)} resultaten."
                )
            urls.append(last[n - 1]["url"])
    return list({norm_url(u): u for u in urls}.values())


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("site_name", metavar="SITE")
@click.option(
    "--timeout", default=300, show_default=True, help="Seconden wachten op de login."
)
@_command
async def login(site_name: str, timeout: int) -> int:
    """Log in op een bron; de sessie blijft bewaard tot de site hem laat verlopen."""
    site = Site.load(site_name)
    env = load_env_file()
    key = site.name.upper().replace("-", "_")
    user, password = (
        env.get(f"DOC_LOOKUP_{key}_USER"),
        env.get(f"DOC_LOOKUP_{key}_PASS"),
    )

    if site.get("login.mode") == "form" and user and password:
        say("[1/2] Automatisch inloggen met gegevens uit ~/.doc-lookup/.env")
        wall = await login_with_form(site, user, password)
        if wall is None:
            say("[2/2] Ingelogd, sessie opgeslagen.")
            return 0
        warn(f"automatisch inloggen mislukt ({wall}); val terug op handmatig")

    say("[1/2] Er opent een browservenster. Log daar in (MFA/SSO werkt gewoon).")
    say(f"      Ik wacht maximaal {timeout}s en zie zelf wanneer je binnen bent.")
    try:
        wall = await login_interactive(site, timeout)
    except BrowserUnavailable as e:
        say(
            "FOUT: kan geen zichtbaar browservenster openen (geen beeldscherm?). Draai deze "
            f"stap op je eigen computer, of gebruik login.mode 'form' met ~/.doc-lookup/.env. ({e})"
        )
        return EXIT_AUTH
    if wall:
        say(f"FOUT: inloggen lijkt mislukt ({wall}). Probeer opnieuw.")
        return EXIT_AUTH
    say(f"[2/2] Ingelogd. Sessie opgeslagen in {site.auth_path}")
    if not site.get("content.selector"):
        say(f"Volgende stap: init {site.base_url} --name {site.name} --refresh")
    return 0


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("site_name", metavar="SITE")
@click.argument("items", nargs=-1, required=True)
@click.option("--no-images", is_flag=True, help="Geen afbeeldingen downloaden.")
@_command
async def fetch(site_name: str, items: tuple[str, ...], no_images: bool) -> int:
    """Haal pagina's op (URL's of nummers uit de laatste zoekactie), volledig, als Markdown."""
    site = Site.load(site_name)
    urls = _targets(site, items)
    t_all = time.monotonic()
    totals = {
        "text": 0,
        "img": 0,
        "imgs": 0,
        "nieuw": 0,
        "gewijzigd": 0,
        "ongewijzigd": 0,
    }
    problems: list[tuple[str, str]] = []
    paths: list[str] = []
    exit_code = 0

    async with open_context(site) as context:
        page = await context.new_page()
        for i, url in enumerate(urls, 1):
            t0 = time.monotonic()
            say(f"[{i}/{len(urls)}] {url}")
            r = await render_page(page, site, url)
            if r.status == "AUTH":
                site.save_index()
                say(
                    f"FOUT: login vereist of sessie verlopen ({r.message}). "
                    f"Voer uit: login {site.name}  en daarna dit fetch-commando opnieuw."
                )
                return EXIT_AUTH
            if r.status != "OK":
                warn(r.message)
                shot = await screenshot(
                    page, site.debug_dir / f"{url_slug(url)}-{r.status.lower()}.png"
                )
                problems.append((url, f"{r.message} | screenshot: {shot}"))
                exit_code = (
                    EXIT_STRUCTURE
                    if r.status == "STRUCTURE"
                    else (exit_code or EXIT_EMPTY)
                )
                continue

            data = r.data
            e = cache.entry(site, url)
            slug = e["slug"] if e else url_slug(url)
            content_imgs = [im for im in data["imgs"] if not is_icon(im)]
            if no_images:
                img_map, saved = {im["idx"]: "" for im in content_imgs}, []
            else:
                img_map, saved = await download_images(
                    context, site, slug, data["imgs"]
                )
            md, info = html_to_markdown(data["html"], img_map)
            comp = completeness(
                data,
                md,
                len(content_imgs),
                len(content_imgs) if no_images else len(saved),
            )
            meta = {"slug": slug, "images": saved, "warnings": comp["warnings"]}
            if r.response is not None:
                meta["etag"] = r.response.headers.get("etag")
                meta["last_modified"] = r.response.headers.get("last-modified")
            change = cache.store_page(site, url, data["title"] or url, md, meta)
            ent = cache.entry(site, url)
            img_tok = sum(s["tokens"] for s in saved)
            totals[change] += 1
            totals["text"] += ent["tokens"]
            totals["img"] += img_tok
            totals["imgs"] += len(saved)
            paths.append(str(cache.page_path(site, slug)))

            extra = []
            if info["complex_tables"]:
                extra.append(f"{info['complex_tables']} complexe tabel(len) als HTML")
            if info["callouts"]:
                extra.append(f"{info['callouts']} callout(s)")
            if info["expands"]:
                extra.append(f"{info['expands']} uitklapblok(ken)")
            say(f"      {data['title'][:80]}")
            say(
                f"      {change} ({time.monotonic() - t0:.1f}s) - {fmt_tokens(ent['tokens'])} tekst"
                + (f", {len(saved)} afb. ({fmt_tokens(img_tok)})" if saved else "")
                + (" | " + ", ".join(extra) if extra else "")
            )
            c = comp["checks"]
            say(
                f"      volledigheid: tekst {c['tekstdekking']} | tabellen {c['tabellen']} | "
                f"lijsten {c['lijstitems']} | code {c['codeblokken']} | koppen {c['koppen']} | "
                f"afb. {c['afbeeldingen']}" + ("" if comp["warnings"] else "  OK")
            )
            for w in comp["warnings"]:
                warn(w)
            if comp["warnings"]:
                shot = await screenshot(page, site.debug_dir / f"{slug}.png")
                say(f"      screenshot ter controle: {shot}")
                problems.append((url, "; ".join(comp["warnings"])))
            for im in saved:
                alt = f" '{im['alt'][:50]}'" if im["alt"] else ""
                say(
                    f"      afb: {im['path']} ({im['w']}x{im['h']}, {fmt_tokens(im['tokens'])}){alt}"
                )
            site.save_index()

    RUN.doc_tokens += totals["text"]
    RUN.img_tokens_max += totals["img"]
    say("")
    say(
        f"Samenvatting ({time.monotonic() - t_all:.1f}s): {len(urls)} pagina's - "
        f"{totals['nieuw']} nieuw, {totals['gewijzigd']} gewijzigd, "
        f"{totals['ongewijzigd']} ongewijzigd"
        + (f", {len(problems)} met problemen" if problems else "")
    )
    say(
        f"Te lezen: {fmt_tokens(totals['text'])} tekst"
        + (
            f" + max. {fmt_tokens(totals['img'])} als alle {totals['imgs']} afbeeldingen bekeken worden"
            if totals["imgs"]
            else ""
        )
    )
    if paths:
        say("Bestanden:")
        for p in paths:
            say(f"  {p}")
    for url, msg in problems:
        say(f"PROBLEEM {url}: {msg}")
    if exit_code == EXIT_STRUCTURE:
        say(
            "Structuur gewijzigd. Herken opnieuw met: init "
            f"{site.get('structure.sample_url', site.base_url)} --name {site.name} --refresh"
        )
    site.log(
        site.usage_path,
        {
            "cmd": "fetch",
            "pages": len(urls),
            "text_tokens": totals["text"],
            "problems": len(problems),
        },
    )
    return exit_code
