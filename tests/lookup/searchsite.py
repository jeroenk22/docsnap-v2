"""Routes van de testsite voor elke zoekmethode van doc-lookup.

Elke sectie bootst een soort documentatiesite na:
- /idx/   MkDocs met search/search_index.json
- /sph/   Sphinx met searchindex.js
- /lt/    een site met llms.txt
- /wiki/  Confluence met de REST-zoek-API
- /hc/    Zendesk Help Center met de zoek-API
- /live/  zoekbalk met live-resultaten via een JSON-API (zoals Algolia)
- /form/  zoekformulier dat naar een resultatenpagina gaat (/form/zoek?q=...)
- /local/ zoekbalk die client-side filtert, zonder netwerkverkeer
- /none/  geen zoekfunctie, alleen een sitemap
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

NAV = "<nav><a href='{p}/start'>Start</a> <a href='{p}/over'>Over ons</a></nav>"


def article(title: str, body: str = "", head: str = "", section: str = "") -> str:
    text = (
        body
        or ("Uitleg over " + title.lower() + " met alle details die je nodig hebt. ")
        * 4
    )
    return (
        f"<html><head><title>{title}</title>{head}</head><body>{NAV.format(p=section)}"
        f"<main class='article-body'><h1>{title}</h1><p>{text}</p></main></body></html>"
    )


def _json(data: object) -> tuple[int, str, str]:
    return 200, json.dumps(data), "application/json"


MKDOCS_INDEX = {
    "docs": [
        {
            "location": "start/",
            "title": "Kleurenschema instellen",
            "text": "Zet de donkere modus aan met palette scheme slate.",
        },
        {
            "location": "start/#donker",
            "title": "Donkere modus",
            "text": "De donkere modus volgt de systeeminstelling.",
        },
        {
            "location": "fonts/",
            "title": "Lettertype kiezen",
            "text": "Kies een eigen lettertype.",
        },
    ]
}

SPHINX_INDEX = (
    "Search.setIndex("
    + json.dumps(
        {
            "docnames": ["index", "csvmod"],
            "filenames": ["index.rst", "csvmod.rst"],
            "titles": ["Welkom", "csv — CSV-bestanden lezen en schrijven"],
            "terms": {"welkom": 0, "pars": [1], "bestand": 1, "csv": 1},
            "titleterms": {"csv": 1, "lezen": 1},
        }
    )
    + ")"
)

LLMS_TXT = """# Voorbeeld-docs

## Handleidingen
- [Eigen domein koppelen](/lt/domein): Koppel een eigen domein aan je site
- [Bezoekersanalyse](/lt/analyse): Statistieken over bezoekers
"""

LIVE_PAGE = article(
    "Handleiding planning",
    head="""<script>
document.addEventListener('DOMContentLoaded', () => {
  const box = document.querySelector('input[type=search]');
  box.addEventListener('input', async () => {
    const r = await fetch('/live/api/search?term=' + encodeURIComponent(box.value) + '&limit=10');
    const j = await r.json();
    document.getElementById('results').innerHTML =
      j.data.hits.map(h => `<li><a href="${h.path}">${h.name}</a> ${h.summary}</li>`).join('');
  });
});
</script>""",
    section="/live",
).replace("<main", "<input type=search placeholder='Zoeken'><ul id=results></ul><main")

FORM_PAGE = article("Handleiding facturatie", section="/form").replace(
    "<main",
    "<form action='/form/zoek'><input name=q placeholder='Zoek in de docs'></form><main",
)

LOCAL_PAGE = article(
    "Handleiding magazijn",
    head="""<script>
const PAGES = [['/local/voorraad', 'Voorraad tellen'], ['/local/handleiding-pakbon', 'Handleiding pakbon']];
document.addEventListener('DOMContentLoaded', () => {
  const box = document.querySelector('input[type=search]');
  box.addEventListener('input', () => {
    const v = box.value.toLowerCase();
    document.getElementById('results').innerHTML = PAGES.filter(p => p[1].toLowerCase().includes(v) || v.length > 3)
      .map(p => `<li><a href="${p[0]}">${p[1]}</a></li>`).join('');
  });
});
</script>""",
    section="/local",
).replace("<main", "<input type=search><ul id=results></ul><main")

SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>{o}/none/orders-verwerken</loc></url>
<url><loc>{o}/none/ritten-plannen</loc></url>
</urlset>"""


def route(path: str, host: str) -> tuple[int, str, str] | None:
    """Antwoord voor een zoekroute, of None als het pad niet van deze testsite is."""
    u = urlparse(path)
    q = parse_qs(u.query)
    origin = f"http://{host}"
    p = u.path

    # Zoals MendriX: onbekende paden geven 403 in plaats van 404
    if p.startswith("/f403/") and p.endswith((".json", ".js", ".txt", ".xml")):
        return 403, "verboden", "text/plain"
    # MkDocs: /idx/start/ en /idx/search/search_index.json
    if p == "/idx/search/search_index.json":
        return _json(MKDOCS_INDEX)
    # Sphinx: /sph/index.html en /sph/searchindex.js
    if p == "/sph/searchindex.js":
        return 200, SPHINX_INDEX, "application/javascript"
    # llms.txt
    if p == "/lt/llms.txt":
        return 200, LLMS_TXT, "text/plain"
    # Confluence
    if p == "/wiki/rest/api/search":
        cql = q.get("cql", [""])[0]
        if 'space = "SP"' not in cql:
            return _json({"results": []})
        return _json(
            {
                "_links": {"base": f"{origin}/wiki"},
                "results": [
                    {
                        "title": "Consumer @@@hl@@@rebalance@@@endhl@@@",
                        "url": "/display/SP/Rebalance",
                        "excerpt": "Hoe een rebalance werkt",
                        "lastModified": "2026-09-01T10:00:00Z",
                    }
                ],
            }
        )
    # Zendesk
    if p == "/api/v2/help_center/articles/search.json":
        return _json(
            {
                "results": [
                    {
                        "html_url": f"{origin}/hc/en-us/articles/2-Macros",
                        "title": "Macros",
                        "snippet": "Use <em>macros</em> to respond",
                        "edited_at": "2026-08-01",
                    }
                ]
            }
        )
    # Live zoek-API
    if p == "/live/api/search":
        term = q.get("term", [""])[0]
        # Zoals een echte zoek-API: alleen pagina's waarin de term voorkomt
        docs = [
            {
                "path": "/live/orders",
                "name": "Orders verwerken",
                "modified": "2026-09-02",
                "summary": "Handleiding voor het verwerken van orders in de planning",
            },
        ]
        hits = [
            d
            for d in docs
            if term and term.lower() in (d["name"] + d["summary"]).lower()
        ]
        return _json({"data": {"hits": hits, "total": len(hits)}})
    # Zoekresultatenpagina
    if p == "/form/zoek":
        term = q.get("q", [""])[0]
        return (
            200,
            (
                f"<html><body>{NAV.format(p='/form')}<main><h1>Resultaten voor {term}</h1>"
                f"<ul><li><a href='/form/factuur-maken'>Factuur maken</a></li></ul>"
                f"<a href='/form/zoek?q={term}&page=2'>Volgende</a></main></body></html>"
            ),
            "text/html",
        )
    if p == "/none/sitemap.xml":
        return 200, SITEMAP.format(o=origin), "application/xml"

    pages = {
        "/idx/start/": article("Kleurenschema instellen", section="/idx"),
        "/sph/index.html": article("Welkom bij de module-documentatie", section="/sph"),
        "/lt/start": article("Snelstart voor beheerders", section="/lt"),
        "/wiki/display/SP/Start": article(
            "Startpagina van de space",
            head=f"<meta name='ajs-base-url' content='{origin}/wiki'>",
            section="/wiki",
        ),
        "/hc/en-us/articles/1": article("Getting started with Zendesk", section="/hc"),
        "/live/start": LIVE_PAGE,
        "/form/start": FORM_PAGE,
        "/local/start": LOCAL_PAGE,
        "/none/start": article("Introductie handleiding", section="/none"),
    }
    if p in pages:
        return 200, pages[p], "text/html"
    for prefix in (
        "/f403/",
        "/idx/",
        "/sph/",
        "/lt/",
        "/wiki/",
        "/hc/",
        "/live/",
        "/form/",
        "/local/",
        "/none/",
    ):
        if p.startswith(prefix) and not p.endswith((".json", ".js", ".txt", ".xml")):
            return (
                200,
                article(
                    p.rsplit("/", 1)[-1].replace("-", " ").title(), section=prefix[:-1]
                ),
                "text/html",
            )
    return None
