"""Tests voor zoeken: parsers en API-herkenning, en elke zoekmethode tegen de testsite."""

import json

import pytest
from click.testing import CliRunner

from src.lookup.cli import cli
from src.lookup.search import (
    _fill,
    _parse_llms,
    _parse_mkdocs,
    _parse_sphinx,
    _prefixes,
    _score_docs,
    _template,
    jget,
    learn_api,
    probe_term,
)
from src.lookup.site import Site

from .searchsite import LLMS_TXT, MKDOCS_INDEX, SPHINX_INDEX


def run(*args: str):
    return CliRunner().invoke(cli, list(args))


# ---------------------------------------------------------------------------
# Unit
# ---------------------------------------------------------------------------


def test_prefixes_walk_up_from_page() -> None:
    assert _prefixes("https://x.com/docs/a/page.html") == [
        "https://x.com/docs/a/",
        "https://x.com/docs/",
        "https://x.com/",
    ]
    assert _prefixes("https://x.com/docs/a/") == [
        "https://x.com/docs/a/",
        "https://x.com/docs/",
        "https://x.com/",
    ]


def test_index_parsers() -> None:
    mk = _parse_mkdocs(json.dumps(MKDOCS_INDEX), "https://x.com/idx/")
    assert mk[0] == {
        "url": "https://x.com/idx/start/",
        "title": "Kleurenschema instellen",
        "text": "Zet de donkere modus aan met palette scheme slate.",
    }
    sph = _parse_sphinx(SPHINX_INDEX, "https://x.com/sph/")
    assert sph[1]["url"] == "https://x.com/sph/csvmod.html"
    assert "pars" in sph[1]["text"]
    lt = _parse_llms(LLMS_TXT, "https://x.com/lt/")
    assert lt[0] == {
        "url": "https://x.com/lt/domein",
        "title": "Eigen domein koppelen",
        "text": "Koppel een eigen domein aan je site",
    }


def test_score_docs_requires_all_terms_and_matches_stems() -> None:
    docs = _parse_sphinx(SPHINX_INDEX, "https://x.com/sph/")
    hits = _score_docs(docs, "parsing csv", 5)  # 'parsing' matcht de stam 'pars'
    assert [h["url"] for h in hits] == ["https://x.com/sph/csvmod.html"]
    assert _score_docs(docs, "csv onbekendwoord", 5) == []
    # Ankers op dezelfde pagina tellen als één resultaat
    mk = _score_docs(
        _parse_mkdocs(json.dumps(MKDOCS_INDEX), "https://x.com/idx/"),
        "donkere modus",
        5,
    )
    assert [h["url"] for h in mk] == ["https://x.com/idx/start/"]


def test_template_and_fill_roundtrip() -> None:
    assert (
        _template("https://x.com/s?q=dark%20mode&n=5", "dark mode")
        == "https://x.com/s?q={q}&n=5"
    )
    assert (
        _template("https://x.com/s?q=dark+mode", "dark mode")
        == "https://x.com/s?q={q+}"
    )
    assert _template('{"query":"dark mode"}', "dark mode") == '{"query":"{qraw}"}'
    assert _fill('{"query":"{qraw}"}', 'a "b"') == '{"query":"a \\"b\\""}'
    assert _fill("q={q}&p={q+}", "a b") == "q=a%20b&p=a+b"


def test_learn_api_picks_result_list_with_urls(lookup_home) -> None:
    site = Site("x")
    site.cfg = {"base_url": "https://docs.x.com/start"}
    captured = [
        {
            "url": "https://docs.x.com/api/config",
            "method": "GET",
            "body": None,
            "headers": {},
            "json": {"a": 1},
        },
        {
            "url": "https://algolia.net/1/indexes/*/queries",
            "method": "POST",
            "body": '{"requests":[{"query":"orders","hitsPerPage":5}]}',
            "headers": {
                "x-algolia-api-key": "pub",
                "content-type": "application/json",
                "cookie": "geheim",
            },
            "json": {
                "results": [
                    {
                        "hits": [
                            {
                                "url": "https://docs.x.com/orders",
                                "hierarchy": {"lvl1": "Orders"},
                                "content": "Over orders",
                                "_highlightResult": {},
                            },
                            {
                                "url": "https://docs.x.com/ritten",
                                "hierarchy": {"lvl1": "Ritten"},
                                "content": "x",
                            },
                        ]
                    }
                ]
            },
        },
    ]
    api = learn_api(captured, "orders", site)
    assert api["method"] == "POST"
    assert api["body"] == '{"requests":[{"query":"{qraw}","hitsPerPage":5}]}'
    assert api["results_path"] == "results.0.hits"
    assert api["url_field"] == "url"
    assert api["headers"] == {
        "x-algolia-api-key": "pub",
        "content-type": "application/json",
    }  # geen cookies
    assert learn_api(captured, "nietgezocht", site) is None


def test_jget_and_probe_term() -> None:
    assert jget({"a": [{"b": "c"}]}, "a.0.b") == "c"
    assert jget({"a": []}, "a.3.b") is None
    assert probe_term("Installatie en configuratie van GLS Link") == "configuratie"
    assert probe_term("API") == "help"


# ---------------------------------------------------------------------------
# Integratie: init herkent de methode, search vindt, fetch haalt op nummer op
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "start,method,query,expected",
    [
        ("/idx/start/", "zoekindex van de site", "donkere modus", "/idx/start/"),
        (
            "/sph/index.html",
            "zoekindex van de site",
            "csv bestanden",
            "/sph/csvmod.html",
        ),
        ("/lt/start", "zoekindex van de site", "eigen domein", "/lt/domein"),
        (
            "/wiki/display/SP/Start",
            "Confluence-zoek-API",
            "rebalance",
            "/wiki/display/SP/Rebalance",
        ),
        (
            "/hc/en-us/articles/1",
            "Zendesk-zoek-API",
            "macros",
            "/hc/en-us/articles/2-Macros",
        ),
        ("/live/start", "zoek-API achter de zoekbalk", "orders", "/live/orders"),
        ("/form/start", "zoekresultatenpagina", "factuur", "/form/factuur-maken"),
        ("/local/start", "zoekbalk", "pakbon", "/local/handleiding-pakbon"),
        ("/none/start", "sitemap", "orders verwerken", "/none/orders-verwerken"),
    ],
)
def test_search_method_end_to_end(docsite, start, method, query, expected) -> None:
    init = run("init", docsite + start, "--name", "bron")
    assert init.exit_code == 0, init.output
    assert f"zoeken via: {method}" in init.output

    result = run("search", "bron", query, "--limit", "5")

    assert result.exit_code == 0, result.output
    assert f"via {method}" in result.output
    assert " 1. " in result.output
    assert docsite + expected in result.output
    assert "Ophalen: fetch bron <nummers>" in result.output


def test_search_then_fetch_by_number(docsite) -> None:
    assert run("init", f"{docsite}/live/start", "--name", "bron").exit_code == 0
    assert run("search", "bron", "orders").exit_code == 0

    fetched = run("fetch", "bron", "1")

    assert fetched.exit_code == 0, fetched.output
    assert f"{docsite}/live/orders" in fetched.output
    assert "Orders" in fetched.output
    missing = run("fetch", "bron", "7")
    assert missing.exit_code != 0
    assert "Resultaat 7 bestaat niet" in missing.output


def test_search_merges_queries_and_marks_cached(docsite) -> None:
    assert run("init", f"{docsite}/idx/start/", "--name", "bron").exit_code == 0
    assert run("fetch", "bron", f"{docsite}/idx/start/").exit_code == 0

    result = run("search", "bron", "donkere modus", "kleurenschema")

    assert "gevonden met: donkere modus, kleurenschema" in result.output
    assert "in cache" in result.output


def test_search_detects_methods_for_older_site(docsite) -> None:
    """Een bron van vóór de zoekfunctie wordt bij de eerste zoekactie herkend."""
    assert run("init", f"{docsite}/idx/start/", "--name", "bron").exit_code == 0
    site = Site.load("bron")
    site.cfg.pop("search")
    site.save_cfg()

    result = run("search", "bron", "lettertype")

    assert "Zoekmogelijkheden herkennen (eenmalig)" in result.output
    assert f"{docsite}/idx/fonts/" in result.output
    assert Site.load("bron").get("search.methods")[0] == "index"


def test_section_bonus_prefers_same_section() -> None:
    from src.lookup.search import section_bonus

    base = "https://learn.microsoft.com/en-us/azure/storage/blobs/intro"
    assert (
        section_bonus("https://learn.microsoft.com/en-us/azure/storage/files/x", base)
        == 0.75
    )
    assert (
        section_bonus("https://learn.microsoft.com/en-us/microsoftteams/x", base)
        == 0.25
    )
    assert (
        section_bonus("https://x.com/space/TMS/1/a", "https://x.com/space/TMS/2/b")
        == 0.5
    )
    assert (
        section_bonus("https://x.com/space/VIS/1/a", "https://x.com/space/TMS/2/b")
        == 0.25
    )


def test_init_treats_403_on_probed_paths_as_absent(docsite) -> None:
    """Sites die op onbekende paden 403 geven (zoals MendriX) mogen init niet laten crashen."""
    result = run("init", f"{docsite}/f403/start", "--name", "bron")

    assert result.exit_code == 0, result.output
    assert "zoeken via:" in result.output


def test_portal_template_from_site_links(lookup_home) -> None:
    from src.lookup.search import portal_template

    site = Site("x")
    site.cfg = {"base_url": "https://support.x.nl/"}
    links = [
        "https://support.x.nl/space/TMS",
        "https://x.atlassian.net/wiki/spaces/TMS/pages/93881915/Titel",
        "https://support.x.nl/space/TMS/1307213836/Installatie+GLS",
    ]
    assert (
        portal_template(links, site, {"TMS"})
        == "https://support.x.nl/space/{space}/{id}"
    )
    assert portal_template(links[:2], site, {"TMS"}) is None
    assert portal_template(links, site, {"VIS"}) is None
