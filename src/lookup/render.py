"""Pagina laden, wachten tot de content er echt staat, en uitlezen.

JS-documentatie (SPA's) laadt vaak ná networkidle: eerst een laadscherm, dan
de tekst. Daarom wachten we op de content-container zelf: die moet bestaan,
genoeg tekst hebben, niet meer groeien en er mag geen spinner meer zichtbaar
zijn. Blijft de pagina leeg, dan wordt dat gemeld en niets opgeslagen.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import re
import time
from dataclasses import dataclass, field

from playwright.async_api import BrowserContext, Frame, Page, Response

from ..scraper import _scroll_to_bottom
from .convert import is_icon
from .site import Site, sha
from .usage import image_tokens

# Bekende content-containers, meest specifiek eerst
DEFAULT_CONTENT_SELECTORS = [
    "#main-content",  # Confluence
    ".wiki-content",
    "#content-body",
    ".article-body",  # Zendesk / Document360
    "article .article-content",
    "article.md-content__inner",  # MkDocs Material
    ".md-content",
    ".theme-doc-markdown",  # Docusaurus
    ".markdown",
    ".markdown-body",  # GitHub-achtig
    ".rst-content [role=main]",  # Sphinx
    ".document",
    "[data-testid='page-content']",
    "main article",
    "article",
    "[role=main]",
    "main",
    "#content",
    ".content",
]

# Maximale wachttijd op stabiele content (seconden); bij een retry het dubbele
STABLE_TIMEOUT = 20.0

# Laadindicatoren; afbeeldingen niet (lazy <img class="loading"> blijft zo tot je scrolt)
LOADER_SELECTORS = (
    "[aria-busy='true']:not(img), [class*='spinner' i]:not(img), "
    "[class*='skeleton' i]:not(img), [class*='loading' i]:not(body):not(html):not(img), "
    ".ak-spinner"
)

# Pagina-specifieke opschoning per platform (binnen de content-container)
PLATFORM_DEFAULTS = {
    "confluence": {
        "title_selector": "#title-text",
        "remove": [
            ".page-metadata",
            "#likes-and-labels-container",
            "#comments-section",
            ".plugin_pagetree",
        ],
    },
    "zendesk": {
        "title_selector": ".article-title",
        "remove": [".article-votes", ".article-subscribe", ".article-share"],
    },
    "mkdocs": {"title_selector": "", "remove": [".headerlink", ".md-source-file"]},
    "docusaurus": {
        "title_selector": "",
        "remove": [".hash-link", ".pagination-nav", ".theme-edit-this-page"],
    },
}

FIND_ROOT_JS = """
(sels) => {
  for (const s of sels) {
    let el = null;
    try { el = document.querySelector(s); } catch (e) { continue; }
    if (el) {
      const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
      return {selector: s, chars: t.length};
    }
  }
  return null;
}
"""

STABILITY_JS = """
([sel, loaders]) => {
  let root = null;
  try { root = document.querySelector(sel); } catch (e) {}
  if (!root) return null;
  const t = (root.textContent || '').replace(/\\s+/g, ' ').trim();
  let busy = 0;
  for (const el of document.querySelectorAll(loaders)) {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'
        && (root.contains(el) || el.contains(root)
            || r.width * r.height > 0.5 * innerWidth * innerHeight)) busy++;  // overlay
  }
  return {chars: t.length, imgs: root.querySelectorAll('img').length, busy};
}
"""

# Kandidaat-containers voor init: bekende selectors plus een dichtheidsheuristiek
CANDIDATES_JS = r"""
(known) => {
  // Alleen gewone class-/id-namen in selectors; Tailwind ('md:flex', 'w-[3px]') niet
  const PLAIN = /^[A-Za-z_][\w-]*$/;
  // Gegenereerde classnamen veranderen bij elke release van de site: styled-components
  // ('StyledRow-sc-xjsdg1-0', 'fJdEWO'), CSS-modules ('Content_body__v5MYy'), emotion ('css-1x2y3z'),
  // en ids per pagina met een UUID of lang nummer ('media_single_container_31f43b4e-ab8d-...')
  const HASHED = /(^|-)sc-[a-z0-9]+|__[A-Za-z0-9_-]{4,}$|^css-[a-z0-9]+$|^(?=[A-Za-z]{5,7}$)(?=.*[a-z])[A-Za-z]+[A-Z][A-Za-z]*$|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}|\d{5,}/;
  const base = (el) => {
    if (el.id && !HASHED.test(el.id)) return '#' + CSS.escape(el.id);
    let s = el.tagName.toLowerCase();
    const cls = [...el.classList].filter(c => PLAIN.test(c) && !HASHED.test(c) && !/^(is-|has-|js-)/.test(c)).slice(0, 2);
    if (cls.length) s += '.' + cls.join('.');
    const role = el.getAttribute('role'); if (role && !cls.length) s += `[role=${role}]`;
    return s;
  };
  const step = (el) => {
    const same = el.parentElement ? [...el.parentElement.children].filter(c => c.tagName === el.tagName) : [];
    return base(el) + (same.length > 1 && !el.id ? `:nth-of-type(${same.indexOf(el) + 1})` : '');
  };
  const unique = (s) => { try { return document.querySelectorAll(s).length === 1; } catch (e) { return false; } };
  // Unieke selector: zo kort mogelijk; dan stabiel met een ouder ervoor ('#main div.content');
  // pas als laatste een pad met posities (kan per pagina verschillen)
  const describe = (el) => {
    const b = base(el);
    if (unique(b)) return b;
    for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) {
      const pb = base(e);
      if ((e.id || pb.includes('.')) && unique(pb + ' ' + b)) return pb + ' ' + b;
    }
    const parts = [step(el)];
    for (let e = el.parentElement; e && e !== document.documentElement; e = e.parentElement) {
      if (unique(parts.join(' > '))) break;
      parts.unshift(e.id ? base(e) : step(e));
    }
    return parts.join(' > ');
  };
  // Dialogen en cookie-/consentbanners zijn nooit de content. Op hele classnamen
  // testen: Tailwind-classes als 'has-[.modal]:hidden' staan op gewone wrappers.
  const OVERLAY_NAME = /^(?:[a-z0-9]+[-_])*(?:dialog|modal|cookie|consent)(?:[-_][a-z0-9]+)*$/i;
  const isOverlay = (el) => {
    for (let e = el; e && e !== document.body; e = e.parentElement) {
      if (e.getAttribute('role') === 'dialog' || e.getAttribute('aria-modal') === 'true') return true;
      if ([...e.classList].some(c => OVERLAY_NAME.test(c)) || OVERLAY_NAME.test(e.id || '')) return true;
    }
    return false;
  };
  const textLen = (el) => (el.innerText || '').replace(/\s+/g, ' ').trim().length;
  const linkLen = (el) => [...el.querySelectorAll('a')].reduce((n, a) => n + (a.innerText || '').length, 0);

  // Tekst zonder links, en zonder navigatie/zijbalken erbinnen (anders wint een wrapper met menu)
  const NAV = 'nav, aside, [role=navigation]';
  const navLen = (el) => [...el.querySelectorAll(NAV)]
    .filter(n => !n.parentElement.closest(NAV) || !el.contains(n.parentElement.closest(NAV)))
    .reduce((n, x) => n + (x.innerText || '').length, 0);
  const score = (el) => { const t = textLen(el); return t * (1 - Math.min(1, linkLen(el) / Math.max(t, 1))) - navLen(el); };

  const found = [];
  const add = (el, how) => {
    if (!el || found.some(f => f.el === el) || isOverlay(el)) return;
    const chars = textLen(el);
    if (chars > 0) found.push({el, how, chars, score: score(el)});
  };
  for (const s of known) { try { add(document.querySelector(s), 'bekend: ' + s); } catch (e) {} }
  [...document.querySelectorAll('div, section, article, main')]
    .filter(el => !el.closest('nav, header, footer, aside') && !isOverlay(el))
    .map(el => ({el, score: score(el)}))
    .sort((a, b) => b.score - a.score).slice(0, 6).forEach(x => add(x.el, 'dichtheid'));

  // Wrapper met (vrijwel) dezelfde tekst als een kandidaat erbinnen: de binnenste is specifieker
  const kept = found.filter(a => !found.some(b => b !== a && a.el.contains(b.el) && b.chars >= 0.97 * a.chars));
  return kept.sort((a, b) => b.score - a.score).slice(0, 8).map(({el, how, chars}) => {
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    return {selector: describe(el), how, chars,
            link_ratio: chars ? +(linkLen(el) / chars).toFixed(2) : 1,
            nav_chars: navLen(el),
            headings: el.querySelectorAll('h1,h2,h3').length, preview: t.slice(0, 140)};
  });
}
"""

EXTRACT_JS = """
([sel, titleSel, removeSels]) => {
  const root = document.querySelector(sel);
  if (!root) return null;
  const clone = root.cloneNode(true);
  for (const r of removeSels) { try { clone.querySelectorAll(r).forEach(e => e.remove()); } catch (e) {} }
  clone.querySelectorAll('script, style, noscript, template').forEach(e => e.remove());
  // Kopieerknoppen bij codeblokken ('Copy', 'Copy to clipboard')
  clone.querySelectorAll('button, [role=button]').forEach(b => {
    if (/^(copy|copied|kopieer|kopiëren|gekopieerd)( to clipboard| code)?!?$/i.test((b.textContent || '').trim())
        || /copy|clipboard/i.test(b.getAttribute('aria-label') || '')) b.remove(); });
  // Permalink-ankers bij koppen (MkDocs, Sphinx, Docusaurus): ¶, #, of een icoon-glyph
  // (icoonfonts gebruiken het privé Unicode-bereik U+E000-U+F8FF)
  clone.querySelectorAll('a.headerlink, a.hash-link').forEach(a => a.remove());
  clone.querySelectorAll('h1 a[href^="#"], h2 a[href^="#"], h3 a[href^="#"], h4 a[href^="#"], h5 a[href^="#"], h6 a[href^="#"]').forEach(a => {
    if (/^[\\s\\u200B-\\u200D\\uFEFF¶#§\\u{1F517}\\uE000-\\uF8FF]*$/u.test(a.textContent || '')) a.remove(); });

  // AI-samenvattings- en 'Ask AI'-widgets van de site zelf horen niet bij de documentatie.
  // Alleen echte classnamen (geen Tailwind zoals 'group/ask-ai'), en nooit iets met koppen
  // of veel tekst: dan is het waarschijnlijk toch content.
  const AI_CLASS = /^(?:[a-z0-9]+[-_])*(?:ai[-_]?summary|ask[-_]?ai)(?:[-_][a-z0-9]+)*$/i;
  clone.querySelectorAll('[class]').forEach(e => {
    if ([...e.classList].some(c => AI_CLASS.test(c)) && !e.querySelector('h1,h2,h3,h4,h5,h6')
        && (e.textContent || '').length < 2000) e.remove(); });
  // Sticky-header-kopieën: een tabel met alleen de koprij, direct gevolgd door de echte tabel
  const rowText = (r) => (r ? r.textContent : '').replace(/\s+/g, ' ').trim();
  const tables = [...clone.querySelectorAll('table')];
  tables.forEach((t, i) => {
    const next = tables[i + 1];
    if (next && t.rows.length <= 2 && next.rows.length > t.rows.length
        && rowText(t.rows[0]) && rowText(t.rows[0]) === rowText(next.rows[0])) t.remove();
  });
  // Ingesloten pagina's van dezelfde site (iframe) meenemen; externe blijven een verwijzing
  const liveFrames = root.querySelectorAll('iframe');
  clone.querySelectorAll('iframe').forEach((f, i) => {
    try {
      const doc = liveFrames[i] && liveFrames[i].contentDocument;
      if (doc && doc.body && doc.body.innerText.trim().length > 20) {
        const d = document.createElement('div');
        d.innerHTML = '<p><strong>[Ingesloten inhoud]</strong></p>' + doc.body.innerHTML;
        d.querySelectorAll('script, style').forEach(e => e.remove());
        f.replaceWith(d);
      }
    } catch (e) {}
  });
  // Tooltips (alleen zichtbaar bij hover) als tekst meenemen
  clone.querySelectorAll('[title], [data-tooltip], [data-tippy-content], [data-original-title]').forEach(el => {
    if (['IMG', 'IFRAME'].includes(el.tagName)) return;
    const tip = (el.getAttribute('data-tooltip') || el.getAttribute('data-tippy-content') ||
                 el.getAttribute('data-original-title') || el.getAttribute('title') || '').trim();
    if (tip && tip.length > 2 && !(el.textContent || '').includes(tip)) el.append(' (' + tip + ')');
  });

  // Afbeeldingen: absolute bron en natuurlijke maat uit de live pagina
  const sizes = {};
  document.querySelectorAll('img').forEach(img => {
    const s = img.currentSrc || img.src; if (s && img.naturalWidth) sizes[s] = [img.naturalWidth, img.naturalHeight]; });
  const imgs = [];
  clone.querySelectorAll('img').forEach((img, i) => {
    const raw = img.getAttribute('src') || img.dataset.src || img.getAttribute('data-image-src') || img.getAttribute('data-lazy-src') || '';
    let abs = '';
    try { abs = raw ? new URL(raw, document.baseURI).href : ''; } catch (e) {}
    const sz = sizes[abs] || [parseInt(img.getAttribute('width') || '0'), parseInt(img.getAttribute('height') || '0')];
    img.setAttribute('data-dl-idx', String(i));
    if (abs) img.setAttribute('src', abs);
    imgs.push({idx: i, src: abs, alt: img.alt || '', w: sz[0], h: sz[1], cls: img.className || ''});
  });
  clone.querySelectorAll('a[href]').forEach(a => {
    try { a.setAttribute('href', new URL(a.getAttribute('href'), document.baseURI).href); } catch (e) {}
  });

  let title = '';
  if (titleSel) { const t = document.querySelector(titleSel); if (t) title = t.innerText.trim(); }
  if (!title) { const h = root.querySelector('h1') || document.querySelector('h1'); if (h) title = h.innerText.trim(); }
  if (!title) title = document.title;

  const meta = {};
  for (const m of document.querySelectorAll('meta[name], meta[property]')) {
    const k = m.getAttribute('name') || m.getAttribute('property');
    if (/^(ajs-page-id|ajs-page-version|ajs-base-url|ajs-space-key|article:modified_time|last-modified|dcterms\\.modified|og:updated_time)$/i.test(k))
      meta[k.toLowerCase()] = m.getAttribute('content');
  }
  const q = (s) => clone.querySelectorAll(s).length;
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  // Tekst met spaties op blokgrenzen (textContent plakt 'Veld'+'Betekenis' aan elkaar)
  const BLOCK = /^(P|DIV|LI|UL|OL|TD|TH|TR|TABLE|THEAD|TBODY|H[1-6]|BR|PRE|BLOCKQUOTE|SECTION|ARTICLE|DT|DD|DL|SUMMARY|DETAILS|FIGCAPTION|FIGURE|HR|ASIDE|HEADER|FOOTER|MAIN|SPAN|CODE|IMG|BUTTON|LABEL)$/;
  const parts = [];
  const walk = (n) => {
    if (n.nodeType === 3) { parts.push(n.data); return; }
    if (n.nodeType !== 1) return;
    const b = BLOCK.test(n.tagName);
    if (b) parts.push(' ');
    for (const c of n.childNodes) walk(c);
    if (b) parts.push(' ');
  };
  walk(clone);
  return {
    html: clone.outerHTML, title, meta, url: location.href,
    text_all: norm(parts.join('')),
    stats: {
      tables: q('table'), li: q('li'), pre: q('pre'),
      headings: q('h1,h2,h3,h4,h5,h6'), iframes: q('iframe'), imgs: imgs.length,
    },
    imgs,
  };
}
"""


def choose_container(cands: list[dict]) -> dict | None:
    """Kies de content-container uit de kandidaten van CANDIDATES_JS.

    Voorkeur: de meest specifieke bekende container (volgorde van
    DEFAULT_CONTENT_SELECTORS, dus .article-body vóór main), als die weinig
    links bevat en minstens 60% van de tekst van de grootste zulke kandidaat.
    Anders de beste kandidaat volgens de dichtheidsheuristiek.
    """
    known = [
        c
        for c in cands
        if c["how"].startswith("bekend: ")
        and c["chars"] >= 200
        and c["link_ratio"] < 0.5
    ]
    if known:
        most = max(c["chars"] for c in known)
        order = {s: i for i, s in enumerate(DEFAULT_CONTENT_SELECTORS)}
        known.sort(
            key=lambda c: order.get(c["how"].removeprefix("bekend: "), len(order))
        )
        return next(c for c in known if c["chars"] >= 0.6 * most)
    return cands[0] if cands else None


def is_positional(selector: str) -> bool:
    """Wijst de selector alleen via posities (geen id of class) naar de container?"""
    last = selector.split(">")[-1].split(" ")[-1]
    return ":nth-of-type" in selector and "#" not in selector and "." not in last


def content_selectors(site: Site) -> list[str]:
    """Selector(s) uit site.yaml ("a || b" of een lijst), anders de standaardlijst."""
    sel = site.get("content.selector")
    if isinstance(sel, str) and sel.strip():
        return [s.strip() for s in sel.split("||") if s.strip()]
    if isinstance(sel, list) and sel:
        return sel
    return DEFAULT_CONTENT_SELECTORS


async def goto(page: Page, url: str) -> Response | None:
    resp = await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    # Sommige SPA's worden nooit idle (websockets, polling)
    with contextlib.suppress(Exception):
        await page.wait_for_load_state("networkidle", timeout=3_000)
    return resp


async def find_root(
    page: Page, selectors: list[str]
) -> tuple[Frame | None, dict | None]:
    """Zoek de content-container in het hoofdframe en in iframes."""
    frames = [page.main_frame] + [f for f in page.frames if f != page.main_frame]
    best: tuple[Frame | None, dict | None] = (None, None)
    for fr in frames:
        try:
            info = await fr.evaluate(FIND_ROOT_JS, selectors)
        except Exception:  # noqa: BLE001  (frame weg of cross-origin)
            continue
        if info and (best[1] is None or info["chars"] > best[1]["chars"]):
            best = (fr, info)
            if fr == page.main_frame and info["chars"] > 0:
                break
    return best


async def wait_until_stable(
    page: Page, site: Site, timeout: float = 20.0
) -> tuple[Frame | None, str | None, int]:
    """Wacht tot de content bestaat, genoeg tekst heeft, niet groeit en niet laadt.

    Returns (frame, selector, tekens); bij time-out de laatste stand, of
    (None, None, 0) als er geen container gevonden is.
    """
    selectors = content_selectors(site)
    min_chars = int(site.get("content.min_chars", 150))
    deadline = time.monotonic() + timeout
    last = None
    stable_since: float | None = None
    frame, info = None, None
    while time.monotonic() < deadline:
        frame, info = await find_root(page, selectors)
        if frame is not None and info:
            m = await frame.evaluate(STABILITY_JS, [info["selector"], LOADER_SELECTORS])
            if m:
                sig = (m["chars"], m["imgs"])
                if m["chars"] >= min_chars and m["busy"] == 0:
                    if sig != last:
                        stable_since = time.monotonic()
                    elif stable_since and time.monotonic() - stable_since >= 1.0:
                        return frame, info["selector"], m["chars"]
                else:
                    stable_since = None
                last = sig
        await asyncio.sleep(0.3)
    if frame is not None and info:
        return frame, info["selector"], info["chars"]
    return None, None, 0


@dataclass
class Rendered:
    """Uitkomst van render_page: status OK, AUTH, STRUCTURE of EMPTY."""

    status: str
    message: str = ""
    data: dict | None = None
    response: Response | None = field(default=None, repr=False)


async def render_page(page: Page, site: Site, url: str, attempt: int = 1) -> Rendered:
    """Laad één pagina en lees de content uit, met bewaking op lege pagina's."""
    from .session import is_auth_wall  # session importeert render

    resp = await goto(page, url)
    if resp is not None and resp.status in (404, 410, 500, 502, 503):
        return Rendered(
            "EMPTY", f"HTTP {resp.status} (pagina bestaat niet of serverfout)"
        )

    # Eenduidige login-muur (401/403, wachtwoordveld) direct melden, zonder eerst
    # tot STABLE_TIMEOUT te wachten op content die nooit komt.
    wall = await is_auth_wall(page, resp, site, root_found=True)
    if wall:
        return Rendered("AUTH", wall, response=resp)

    min_chars = int(site.get("content.min_chars", 150))
    frame, sel, chars = await wait_until_stable(
        page, site, timeout=STABLE_TIMEOUT * attempt
    )
    wall = await is_auth_wall(page, resp, site, bool(sel and chars >= min_chars))
    if wall:
        return Rendered("AUTH", wall, response=resp)

    if not sel or chars < min_chars:
        configured = site.get("content.selector")
        if configured and not sel:
            # Staat er wel duidelijk content, dan is de structuur veranderd en helpt
            # langer wachten niet
            cands = await page.evaluate(CANDIDATES_JS, DEFAULT_CONTENT_SELECTORS)
            big = [c for c in cands if c["chars"] > 300 and c["link_ratio"] < 0.5]
            if big:
                return Rendered(
                    "STRUCTURE",
                    f"selector '{configured}' vindt niets; kandidaat: {big[0]['selector']}",
                )
        if attempt == 1:  # laadt traag: nog eens, met langer wachten en scrollen
            await _scroll_to_bottom(page)
            return await render_page(page, site, url, attempt=2)
        return Rendered("EMPTY", f"pagina bleef leeg ({chars} tekens na 2 pogingen)")

    await _scroll_to_bottom(page)  # lazy afbeeldingen en content laden
    data = await frame.evaluate(
        EXTRACT_JS,
        [
            sel,
            site.get("content.title_selector", ""),
            site.get("content.remove", []) or [],
        ],
    )
    if data is None:
        return Rendered("EMPTY", "content-container verdween tijdens uitlezen")
    return Rendered("OK", data=data, response=resp)


async def download_images(
    context: BrowserContext, site: Site, slug: str, imgs: list[dict]
) -> tuple[dict[int, str], list[dict]]:
    """Sla content-afbeeldingen op met de sessie van de browser (ook achter login).

    Returns (img_map, saved): img_map idx -> pad relatief aan de pagina ('' als
    de download mislukte; iconen ontbreken), saved = info per opgeslagen bestand.
    """
    limit = asyncio.Semaphore(6)

    async def get(src: str) -> tuple[bytes, str] | None:
        try:
            if src.startswith("data:"):
                head, b64 = src.split(",", 1)
                return base64.b64decode(b64), head.split(";")[0][5:]
            async with limit:
                r = await context.request.get(src, timeout=20_000)
                ctype = r.headers.get("content-type", "")
                # Een loginpagina (redirect, 200 + HTML) is geen afbeelding
                if not r.ok or not ctype.startswith("image/"):
                    return None
                return await r.body(), ctype
        except Exception:  # noqa: BLE001  (convert meldt hem als niet opgehaald)
            return None

    wanted = [im for im in imgs if not is_icon(im) and im["src"]]
    results = await asyncio.gather(*(get(im["src"]) for im in wanted))

    img_map: dict[int, str] = {}
    saved: list[dict] = []
    out_dir = site.images_dir / slug
    for im, result in zip(wanted, results, strict=True):
        if result is None:
            img_map[im["idx"]] = ""
            continue
        body, ctype = result
        ext = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/svg+xml": "svg",
        }.get(ctype.split(";")[0].strip(), "png")
        if len(body) < 300 and ext != "svg":
            continue  # spacer of tracking-pixel
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"{sha(im['src'])[:10]}.{ext}"
        (out_dir / name).write_bytes(body)
        img_map[im["idx"]] = f"../images/{slug}/{name}"
        saved.append(
            {
                "path": str(out_dir / name),
                "w": im["w"],
                "h": im["h"],
                "alt": im["alt"],
                "tokens": image_tokens(im["w"], im["h"]) if ext != "svg" else 0,
            }
        )
    return img_map, saved


async def screenshot(page: Page, path) -> str | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:  # noqa: BLE001
        return None


def detect_platform(html: str, url: str) -> str:
    h = html[:200_000].lower()
    if "ajs-base-url" in h or (
        "confluence" in h and ("atlassian" in h or "wiki-content" in h)
    ):
        return "confluence"
    if "zendesk" in h or re.search(r"/hc/[a-z-]+/articles/", url):
        return "zendesk"
    if 'name="generator" content="mkdocs' in h:
        return "mkdocs"
    for name in ("docusaurus", "document360", "gitbook"):
        if name in h:
            return name
    return "generic"
