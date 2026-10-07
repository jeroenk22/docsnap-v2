"""HTML -> Markdown zonder informatieverlies, plus een volledigheidscontrole.

Uitgangspunten:
- Deterministisch: geen LLM, geen API-key, geen afkapping van lange pagina's.
- Tabellen die niet verliesvrij in Markdown passen (colspan/rowspan, lijsten in
  cellen, geneste tabellen) blijven als opgeschoonde HTML staan.
- Callouts en uitklapblokken worden gelabelde quotes, zodat ze opvallen.
- Afbeeldingen worden lokale bestanden; het relatieve pad staat in de Markdown.
"""

from __future__ import annotations

import re
from collections import Counter

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from markdownify import MarkdownConverter

ICON_HINT = re.compile(r"(emoji|icon|avatar|spinner|badge|status-macro)", re.I)

# Volgorde = prioriteit; 'info' als laatste (zit ook in 'confluence-information-macro')
CALLOUT_LABELS = {
    "warning": "Waarschuwing",
    "note": "Let op",
    "error": "Fout/Waarschuwing",
    "danger": "Waarschuwing",
    "caution": "Let op",
    "tip": "Tip",
    "success": "Tip",
    "important": "Belangrijk",
    "example": "Voorbeeld",
    "question": "Vraag",
    "abstract": "Samenvatting",
    "quote": "Citaat",
    "information": "Info",
    "info": "Info",
}

CALLOUT_SELECTOR = (
    ".confluence-information-macro, [data-panel-type], .panel:not(.code), "
    ".admonition, .alert, [class*=callout], [role=note], [role=alert], aside"
)
CALLOUT_TITLE_SELECTOR = (
    ".confluence-information-macro-title, .admonition-title, "
    ".alert-heading, [class*=callout-title], .panelHeader"
)

KEEP_ATTRS = {"colspan", "rowspan", "href", "src", "alt", "title"}


def is_icon(img: dict) -> bool:
    """Kleine of als icoon gemarkeerde afbeeldingen dragen geen inhoud."""
    w, h = img.get("w") or 0, img.get("h") or 0
    if 0 < w <= 32 and 0 < h <= 32:
        return True
    return bool(ICON_HINT.search(img.get("cls", "") or "")) and w <= 64 and h <= 64


def _label_from_classes(classes: list[str], default: str) -> str:
    joined = " ".join(classes).lower()
    for key, label in CALLOUT_LABELS.items():
        if re.search(rf"(^|[-_ ]){key}($|[-_ ])", joined):
            return label
    return default


def _labeled(soup: BeautifulSoup, text: str) -> Tag:
    """<p><strong>text</strong></p>"""
    p = soup.new_tag("p")
    s = soup.new_tag("strong")
    s.string = text
    p.append(s)
    return p


def _wrap_callout(
    soup: BeautifulSoup, el: Tag, label: str, title_el: Tag | None
) -> None:
    title = title_el.get_text(" ", strip=True) if title_el else ""
    if title_el:
        title_el.decompose()
    bq = soup.new_tag("blockquote")
    bq.append(_labeled(soup, f"{label}: {title}" if title else f"{label}:"))
    for child in list(el.children):
        bq.append(child.extract())
    el.replace_with(bq)


def _clean_table_html(table: Tag) -> None:
    """Alleen wat inhoud draagt: geen styling, colgroups, spans of colspan="1"."""
    for el in table.find_all(["colgroup", "col"]):
        el.decompose()
    for el in table.find_all("span"):
        el.unwrap()
    for t in [table, *table.find_all(True)]:
        t.attrs = {
            k: v
            for k, v in t.attrs.items()
            if k in KEEP_ATTRS and not (k in ("colspan", "rowspan") and str(v) == "1")
        }


def _table_is_complex(table: Tag) -> bool:
    for cell in table.find_all(["td", "th"]):
        try:
            if int(cell.get("colspan", 1)) > 1 or int(cell.get("rowspan", 1)) > 1:
                return True
        except ValueError:
            return True
        if cell.find(
            ["table", "ul", "ol", "pre", "blockquote", "h1", "h2", "h3", "h4"]
        ):
            return True
        if len(cell.find_all("p")) > 1:
            return True
    return False


class _DocConverter(MarkdownConverter):
    def convert_pre(self, el, text, parent_tags=None, **kwargs):
        """Codeblok met taal uit de class (language-x, lang-x, brush: x)."""
        code = el.get_text()
        classes = " ".join(
            el.get("class", []) + (el.code.get("class", []) if el.code else [])
        )
        params = el.get("data-syntaxhighlighter-params") or ""
        m = re.search(r"(?:language-|lang-|brush:\s*)([\w+#-]+)", f"{classes} {params}")
        lang = m.group(1) if m else ""
        fence = "````" if "```" in code else "```"
        return f"\n\n{fence}{lang}\n{code.rstrip()}\n{fence}\n\n"


def _md(html: str) -> str:
    return _DocConverter(
        heading_style="ATX",
        bullets="-",
        strong_em_symbol="*",
        escape_underscores=False,
        escape_asterisks=False,
        escape_misc=False,
        table_infer_header=True,
        newline_style="BACKSLASH",
    ).convert(html)


def _replace_images(soup: BeautifulSoup, img_map: dict[int, str]) -> None:
    for img in soup.find_all("img"):
        idx = img.get("data-dl-idx")
        local = img_map.get(int(idx)) if idx is not None and idx.isdigit() else None
        if local is None:  # icoon of niet relevant
            alt = img.get("alt", "").strip()
            img.replace_with(f" {alt} " if alt and len(alt) < 40 else "")
        elif local == "":  # download mislukt: wel melden, niet stil verliezen
            img.replace_with(f"[afbeelding niet opgehaald: {img.get('src', '')}]")
        else:
            img.attrs = {"src": local, "alt": img.get("alt", "") or f"afbeelding {idx}"}


def _label_expands(soup: BeautifulSoup) -> int:
    """Uitklapblokken (Confluence expand-macro, <details>) als gelabelde blokken."""
    n = 0
    for el in soup.select(".expand-container"):
        title = el.select_one(".expand-control-text, .expand-control")
        body = el.select_one(".expand-content") or el
        title_txt = title.get_text(" ", strip=True) if title else ""
        if title:
            title.decompose()
        new = soup.new_tag("div")
        new.append(_labeled(soup, f"[Uitklapblok] {title_txt}".strip()))
        for child in list(body.children):
            new.append(child.extract())
        el.replace_with(new)
        n += 1
    for el in soup.find_all("details"):
        summ = el.find("summary")
        txt = summ.get_text(" ", strip=True) if summ else ""
        if summ:
            summ.decompose()
        el.insert(0, _labeled(soup, f"[Uitklapblok] {txt}".strip()))
        el.name = "div"
        n += 1
    return n


def html_to_markdown(html: str, img_map: dict[int, str]) -> tuple[str, dict]:
    """Converteer content-HTML naar Markdown.

    img_map: data-dl-idx -> lokaal relatief pad, of '' als de download mislukte.
    Afbeeldingen die er niet in staan gelden als icoon en vervallen.
    Returns (markdown, info) met aantallen complexe tabellen, callouts, enz.
    """
    soup = BeautifulSoup(html, "html.parser")
    info = {"complex_tables": 0, "callouts": 0, "expands": 0, "iframes": 0}

    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()
    for el in soup.find_all("button"):
        if not el.get_text(strip=True):  # sorteer- en icoonknoppen
            el.decompose()
    for el in soup.select(
        "button[class*=copy i], button[class*=clipboard i], [class*=copy-button i]"
    ):
        el.decompose()
    for el in soup.find_all("svg"):
        label = el.get("aria-label") or (
            el.title.get_text(strip=True) if el.title else ""
        )
        el.replace_with(f"[{label}]" if label else "")

    _replace_images(soup, img_map)

    for el in soup.find_all(["iframe", "video"]):
        src = el.get("src") or (el.source.get("src") if el.find("source") else "")
        el.replace_with(f"[Ingesloten {el.name}: {src}]")
        info["iframes"] += 1

    info["expands"] = _label_expands(soup)

    for el in soup.select(CALLOUT_SELECTOR):
        if not el.parent:  # al vervangen via een ouder
            continue
        if not el.get_text(strip=True) and not el.find("img"):
            el.decompose()  # lege meldingscomponent van de site zelf
            continue
        classes = el.get("class", []) + [el.get("data-panel-type", "")]
        for icon in el.select(".confluence-information-macro-icon, .aui-icon"):
            icon.decompose()
        _wrap_callout(
            soup,
            el,
            _label_from_classes(classes, "Opmerking"),
            el.select_one(CALLOUT_TITLE_SELECTOR),
        )
        info["callouts"] += 1

    # Complexe tabellen als opgeschoonde HTML; alleen de buitenste
    raw_blocks: dict[str, str] = {}
    for table in soup.find_all("table"):
        if table.find_parent("table") or not _table_is_complex(table):
            continue
        _clean_table_html(table)
        key = f"DLRAWTABLE{len(raw_blocks)}X"
        raw_blocks[key] = str(table)
        table.replace_with(NavigableString(f"\n\n{key}\n\n"))
        info["complex_tables"] += 1

    # Pipes in eenvoudige tabelcellen escapen, anders breekt de Markdown-tabel
    for cell in soup.find_all(["td", "th"]):
        for t in cell.find_all(string=True):
            if "|" in t and not t.find_parent(["pre", "code"]):
                t.replace_with(t.replace("|", "\\|"))

    md = _md(str(soup))
    for key, html_table in raw_blocks.items():
        md = md.replace(
            key, "<!-- complexe tabel, als HTML behouden -->\n" + html_table + "\n\n"
        )

    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"
    return md, info


# ---------------------------------------------------------------------------
# Volledigheidscontrole
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+")

# Alleen HTML-tags van behouden tabellen; XML/HTML in codevoorbeelden blijft staan
_MD_HTML_TAGS = re.compile(
    r"</?(?:table|thead|tbody|tfoot|tr|td|th|ul|ol|li|p|br|strong|b|em|i|a|img|code"
    r"|pre|div|span|h[1-6]|blockquote|sup|sub|caption|colgroup|col)\b[^>]*>|<!--.*?-->",
    re.S,
)


def _words(text: str) -> Counter:
    return Counter(w.lower() for w in _WORD.findall(text))


def _md_text(md: str) -> str:
    t = _MD_HTML_TAGS.sub(" ", md)
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r" \1 ", t)  # afbeelding-alt
    t = re.sub(r"\]\((https?://|\.\./)[^)]*\)", "] ", t)  # link-urls
    for entity, char in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">")):
        t = t.replace(entity, char)
    return t


def completeness(dom: dict, md: str, images_expected: int, images_saved: int) -> dict:
    """Vergelijk wat de browser in de content zag met wat in de Markdown staat."""
    dom_words = _words(dom.get("text_all", ""))
    md_words = _words(_md_text(md))
    total = sum(dom_words.values())
    kept = sum(min(c, md_words.get(w, 0)) for w, c in dom_words.items())
    coverage = kept / total if total else 0.0
    missing = [w for w, _ in (dom_words - md_words).most_common(10)]

    s = dom.get("stats", {})
    md_tables = len(re.findall(r"^\s*\|?\s*:?-{3,}:?\s*\|", md, re.M)) + md.count(
        "<table"
    )
    # Fences in callouts (> ```) en lijsten (  ```) tellen ook mee
    md_pre = len(re.findall(r"^[ \t>]*(```|````)", md, re.M)) // 2 + md.count("<pre")
    # Ook binnen callouts (> ) en ingesprongen blokken meetellen, maar niet in codeblokken
    prose = re.sub(r"^([ \t>]*)(```+).*?^\1\2[ \t]*$", "", md, flags=re.M | re.S)
    md_li = len(re.findall(r"^[ \t>]*(?:[-*+]|\d+[.)])\s+", prose, re.M)) + prose.count(
        "<li"
    )
    md_head = len(re.findall(r"^[ \t>]*#{1,6}\s", prose, re.M)) + len(
        re.findall(r"<h[1-6]", prose)
    )

    checks = {
        "tekstdekking": f"{coverage * 100:.1f}%",
        "tabellen": f"{md_tables}/{s.get('tables', 0)}",
        "codeblokken": f"{md_pre}/{s.get('pre', 0)}",
        "lijstitems": f"{md_li}/{s.get('li', 0)}",
        "koppen": f"{md_head}/{s.get('headings', 0)}",
        "afbeeldingen": f"{images_saved}/{images_expected}",
    }
    warnings = []
    if coverage < 0.97 and total > 20:
        warnings.append(
            f"tekstdekking {coverage * 100:.1f}% (ontbrekend o.a.: {', '.join(missing)})"
        )
    if md_tables < s.get("tables", 0):
        warnings.append(
            f"minder tabellen in Markdown ({md_tables}) dan op de pagina ({s['tables']})"
        )
    if md_pre < s.get("pre", 0):
        warnings.append(f"codeblokken {md_pre}/{s['pre']}")
    # Lijststructuur pas melden als er ook tekst mist; anders zijn het lijstjes in
    # code-annotaties of tabbladkoppen die als tekst behouden zijn
    if s.get("li", 0) and md_li < s["li"] * 0.9 and coverage < 0.995:
        warnings.append(f"lijstitems {md_li}/{s['li']}")
    if images_saved < images_expected:
        warnings.append(
            f"{images_expected - images_saved} afbeelding(en) niet opgehaald"
        )
    if s.get("iframes"):
        warnings.append(
            f"{s['iframes']} ingesloten frame(s)/video: inhoud daarvan staat niet in de tekst"
        )
    return {"coverage": coverage, "checks": checks, "warnings": warnings}
