"""Tests voor bron-config (site), tokenteller (usage) en paginaopslag (cache)."""

import os
import stat
import sys

import pytest

from src.lookup import cache
from src.lookup.site import (
    Site,
    SiteNotFound,
    default_name,
    list_sites,
    load_env_file,
    norm_url,
    private_dir,
    url_slug,
)
from src.lookup.usage import RUN, fmt_tokens, image_tokens, session_line, text_tokens


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://support.mendrix.nl/space/TMS", "mendrix"),
        ("https://docs.example.com/x", "example"),
        ("https://www.mkdocs.org/user-guide/", "mkdocs"),
        ("http://127.0.0.1:8765/docs", "docs"),
    ],
)
def test_default_name(url: str, expected: str) -> None:
    assert default_name(url) == expected


def test_norm_url_and_slug() -> None:
    assert norm_url("HTTPS://X.com/a/b/#frag") == "https://x.com/a/b"
    assert norm_url("https://x.com") == "https://x.com/"
    slug = url_slug("https://x.com/Docs/Mijn Pagina?id=3")
    assert slug.startswith("docs-mijn-pagina-id-3-")
    assert url_slug("https://x.com/a#1") == url_slug("https://x.com/a/")


def test_site_roundtrip_and_get(lookup_home) -> None:
    site = Site("demo")
    site.cfg = {"base_url": "https://x.com", "content": {"selector": "main"}}
    site.save_cfg()
    site.index = {"k": {"slug": "s"}}
    site.save_index()

    loaded = Site.load("demo")
    assert loaded.base_url == "https://x.com"
    assert loaded.get("content.selector") == "main"
    assert loaded.get("content.missing", 7) == 7
    assert loaded.get("base_url.deeper") is None
    assert loaded.index == {"k": {"slug": "s"}}
    assert list_sites() == ["demo"]
    assert not loaded.has_auth()


def test_site_load_unknown_raises(lookup_home) -> None:
    with pytest.raises(SiteNotFound, match="bestaat nog niet"):
        Site.load("onbekend")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-rechten")
def test_private_dir_is_owner_only(tmp_path) -> None:
    d = private_dir(tmp_path / "auth")
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700


def test_load_env_file_env_vars_win(lookup_home, monkeypatch) -> None:
    lookup_home.mkdir(parents=True)
    (lookup_home / ".env").write_text(
        "# commentaar\nDOC_LOOKUP_X_USER='uit-bestand'\nDOC_LOOKUP_X_PASS=\"p\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOC_LOOKUP_X_USER", "uit-env")
    env = load_env_file()
    assert env["DOC_LOOKUP_X_USER"] == "uit-env"
    assert env["DOC_LOOKUP_X_PASS"] == "p"


def test_token_estimates() -> None:
    assert text_tokens("") == 0
    assert text_tokens("x" * 7) == 2
    assert image_tokens(0, 0) == 1600
    assert image_tokens(640, 360) == 308
    assert image_tokens(4000, 4000) <= 1534  # begrensd op ~1,15 megapixel
    assert fmt_tokens(12345) == "~12.345 tokens"


def test_session_line_accumulates_per_chat(lookup_home, monkeypatch) -> None:
    monkeypatch.setattr(RUN, "out_chars", 35)
    monkeypatch.setattr(RUN, "doc_tokens", 100)
    monkeypatch.setattr(RUN, "img_tokens_max", 0)
    assert session_line(None) == "[tokens] deze stap ~110"
    assert (
        session_line("abc") == "[tokens] deze stap ~110 | hele chat via doc-lookup ~110"
    )
    monkeypatch.setattr(RUN, "img_tokens_max", 300)
    line = session_line("abc")
    assert "hele chat via doc-lookup ~220" in line
    assert "max ~300" in line


def test_store_page_new_unchanged_changed(lookup_home) -> None:
    site = Site("demo")
    url = "https://x.com/a"
    meta = {"slug": "a-1", "images": []}

    assert (
        cache.store_page(site, url, "Titel", "# Titel\n\nTekst één.\n", meta) == "nieuw"
    )
    text = cache.page_path(site, "a-1").read_text(encoding="utf-8")
    assert text.startswith("<!-- doc-lookup | bron: https://x.com/a |")
    assert text.count("# Titel") == 1  # dubbele titel uit de body weggehaald
    assert "Bron: https://x.com/a" in text

    same = "# Titel\n\nTekst   één.\n"  # alleen whitespace anders
    assert cache.store_page(site, url, "Titel", same, meta) == "ongewijzigd"
    assert (
        cache.store_page(site, url + "/", "Titel", "Nieuwe tekst.", meta) == "gewijzigd"
    )
    entry = cache.entry(site, url)
    assert entry["slug"] == "a-1" and entry["tokens"] > 0
