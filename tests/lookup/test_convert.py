"""Tests voor de verliesvrije HTML->Markdown-conversie en de volledigheidscontrole."""

from src.lookup.convert import completeness, html_to_markdown, is_icon


def test_callout_becomes_labeled_quote() -> None:
    html = (
        "<div class='admonition warning'><p class='admonition-title'>Pas op</p>"
        "<p>Niet verwijderen.</p></div>"
    )
    md, info = html_to_markdown(html, {})
    assert "> **Waarschuwing: Pas op**" in md
    assert "> Niet verwijderen." in md
    assert info["callouts"] == 1


def test_callout_without_title_uses_generic_label() -> None:
    md, _ = html_to_markdown("<aside><p>Zomaar een opmerking.</p></aside>", {})
    assert "> **Opmerking:**" in md


def test_empty_callout_dropped() -> None:
    md, info = html_to_markdown(
        "<div class='p-message-info' role='alert'><div></div></div><p>Tekst</p>", {}
    )
    assert md.strip() == "Tekst"
    assert info["callouts"] == 0


def test_complex_table_html_is_cleaned() -> None:
    html = (
        "<table><colgroup><col/></colgroup><tr><th colspan='1' rowspan='1'><p><span>A</span>"
        "</p><button><span></span></button></th><th>B</th></tr><tr><td><p>x</p><p>y</p>"
        "<!-- opmerking --></td><td>z</td></tr></table>"
    )
    md, info = html_to_markdown(html, {})
    assert info["complex_tables"] == 1
    assert "<table><tr><th><p>A</p></th><th>B</th></tr>" in md
    assert "colgroup" not in md and "<span" not in md and "button" not in md
    assert "opmerking" not in md


def test_complex_table_kept_as_html() -> None:
    html = (
        "<table class='x'><tr><th rowspan='2' style='color:red'>Type</th>"
        "<th colspan='2'>Status</th></tr><tr><th>Voor</th><th>Na</th></tr>"
        "<tr><td>Laden</td><td>Gepland</td><td>Geladen</td></tr></table>"
    )
    md, info = html_to_markdown(html, {})
    assert info["complex_tables"] == 1
    assert '<th rowspan="2">Type</th>' in md  # attributen opgeschoond, spans behouden
    assert "style=" not in md and "class=" not in md


def test_simple_table_escapes_pipes() -> None:
    html = "<table><tr><th>Veld</th><th>Waarde</th></tr><tr><td>a|b</td><td>1</td></tr></table>"
    md, info = html_to_markdown(html, {})
    assert info["complex_tables"] == 0
    assert "| a\\|b | 1 |" in md


def test_details_and_confluence_expand_become_labeled_blocks() -> None:
    html = (
        "<details><summary>Meer info</summary><p>Verborgen tekst</p></details>"
        "<div class='expand-container'><span class='expand-control-text'>Webhook</span>"
        "<div class='expand-content'><p>Stel een URL in.</p></div></div>"
    )
    md, info = html_to_markdown(html, {})
    assert "**[Uitklapblok] Meer info**" in md
    assert "**[Uitklapblok] Webhook**" in md
    assert "Verborgen tekst" in md and "Stel een URL in." in md
    assert info["expands"] == 2


def test_code_block_language_and_nested_fences() -> None:
    md, _ = html_to_markdown(
        "<pre><code class='language-python'>print('hi')</code></pre>"
        "<pre class='lang-md'>```\nx\n```</pre>",
        {},
    )
    assert "```python\nprint('hi')\n```" in md
    assert "````md\n```\nx\n```\n````" in md


def test_images_saved_failed_and_icons() -> None:
    html = (
        "<p><img data-dl-idx='0' src='http://x/a.png' alt='Scherm'>"
        "<img data-dl-idx='1' src='http://x/b.png'>"
        "<img data-dl-idx='2' src='http://x/i.png' alt='ok'></p>"
    )
    md, _ = html_to_markdown(html, {0: "../images/p/a.png", 1: ""})
    assert "![Scherm](../images/p/a.png)" in md
    assert "[afbeelding niet opgehaald: http://x/b.png]" in md
    assert "i.png" not in md and " ok " in md.replace("\n", " ")


def test_svg_and_copy_buttons_removed() -> None:
    html = (
        "<p>Klik <svg aria-label='Opslaan'></svg> en <svg></svg>.</p>"
        "<button class='copy-btn'>Kopieer</button>"
    )
    md, _ = html_to_markdown(html, {})
    assert "[Opslaan]" in md
    assert "Kopieer" not in md


def test_iframe_and_video_referenced() -> None:
    md, info = html_to_markdown(
        "<iframe src='https://youtube.com/x'></iframe><video><source src='v.mp4'></video>",
        {},
    )
    assert "[Ingesloten iframe: https://youtube.com/x]" in md
    assert "[Ingesloten video: v.mp4]" in md
    assert info["iframes"] == 2


def test_is_icon() -> None:
    assert is_icon({"w": 16, "h": 16})
    assert is_icon({"w": 48, "h": 48, "cls": "emoticon"})
    assert not is_icon({"w": 48, "h": 48, "cls": "screenshot"})
    assert not is_icon({"w": 0, "h": 0})


def _dom(text: str, **stats: int) -> dict:
    return {"text_all": text, "stats": stats}


def test_completeness_full_coverage_is_ok() -> None:
    text = "Open instellingen en kies gebeurtenissen voor het type order " * 3
    md = f"## Stappen\n\n{text}\n\n- een\n- twee\n\n> ```sql\n> SELECT 1;\n> ```\n"
    result = completeness(_dom(text, headings=1, li=2, pre=1), md, 0, 0)
    assert result["warnings"] == []
    assert result["checks"]["codeblokken"] == "1/1"  # fence in een callout telt mee


def test_completeness_reports_missing_text_tables_and_images() -> None:
    text = " ".join(f"woord{i}" for i in range(40))
    result = completeness(
        _dom(text, tables=2, pre=1, li=10, iframes=1), "alleen dit", 3, 1
    )
    joined = " | ".join(result["warnings"])
    assert "tekstdekking 0.0%" in joined
    assert "minder tabellen" in joined
    assert "codeblokken 0/1" in joined
    assert "lijstitems 0/10" in joined
    assert "2 afbeelding(en) niet opgehaald" in joined
    assert "ingesloten frame" in joined


def test_choose_container_prefers_most_specific_known_selector() -> None:
    from src.lookup.render import choose_container

    def cand(how: str, chars: int, link_ratio: float = 0.05) -> dict:
        return {
            "selector": how.split(": ")[-1],
            "how": how,
            "chars": chars,
            "link_ratio": link_ratio,
        }

    main = cand("bekend: main", 10_000)
    article = cand("bekend: .article-body", 8_000)
    tiny = cand("bekend: .wiki-content", 1_000)
    assert choose_container([main, tiny, article]) is article  # specifiek en >= 60%
    assert choose_container([main, tiny]) is main  # .wiki-content te klein
    nav = cand("bekend: #content", 5_000, link_ratio=0.8)
    dense = cand("dichtheid", 4_000)
    assert choose_container([nav, dense]) is nav  # beste score als niets bekends past
    assert choose_container([]) is None


def test_completeness_ignores_headings_and_lists_inside_code() -> None:
    md = "## Echt\n\n```yaml\n# commentaar\n- item: 1\n```\n\n- echt item\n"
    result = completeness(
        {"text_all": "Echt echt item", "stats": {"headings": 1, "li": 1}}, md, 0, 0
    )
    assert result["checks"]["koppen"] == "1/1"
    assert result["checks"]["lijstitems"] == "1/1"


def test_is_positional() -> None:
    from src.lookup.render import is_positional

    assert is_positional("div:nth-of-type(4) > div > div:nth-of-type(3) > div")
    assert not is_positional("div.content:nth-of-type(6)")
    assert not is_positional("#main > div:nth-of-type(2)")
    assert not is_positional("div.markdown-body")


def test_markdown_helpers() -> None:
    from src.lookup.convert import (
        localize_markdown_images,
        markdown_images,
        markdown_title,
    )

    md = (
        '# Titel\n\n![a](img/a.png) en ![b](https://x.com/b.png "tip") en ![c](c.png)\n'
    )
    assert markdown_title(md, "x") == "Titel"
    assert markdown_title("geen kop", "x") == "x"
    imgs = markdown_images(md, "https://x.com/docs/page.md")
    assert [i["src"] for i in imgs] == [
        "https://x.com/docs/img/a.png",
        "https://x.com/b.png",
        "https://x.com/docs/c.png",
    ]
    out = localize_markdown_images(md, {0: "../images/p/a.png", 1: ""})
    assert "![a](../images/p/a.png)" in out
    assert "[afbeelding niet opgehaald: https://x.com/b.png]" in out
    assert "![c](c.png)" in out  # geen download geprobeerd (icoon): ongewijzigd
