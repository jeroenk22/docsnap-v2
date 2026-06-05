"""Tests voor de exporter module."""
import pytest

from src.exporter import _url_to_filename, export


@pytest.fixture
def two_pages() -> list[dict]:
    return [
        {
            "url": "https://docs.example.com/page1",
            "title": "Page 1",
            "markdown": "# Page 1\nContent 1",
        },
        {
            "url": "https://docs.example.com/page2",
            "title": "Page 2",
            "markdown": "# Page 2\nContent 2",
        },
    ]


def test_export_combined_markdown(tmp_path, two_pages):
    """Gecombineerd Markdown bestand wordt aangemaakt."""
    export(two_pages, "markdown", tmp_path, project_name="test-docs")

    out_file = tmp_path / "test-docs.md"
    assert out_file.exists()
    content = out_file.read_text()
    assert "Page 1" in content
    assert "Page 2" in content
    assert "docsnap-v2" in content


def test_export_per_file(tmp_path, two_pages):
    """Losse bestanden per pagina worden aangemaakt."""
    export(two_pages, "files", tmp_path)

    files_dir = tmp_path / "pages"
    assert files_dir.exists()
    md_files = list(files_dir.glob("*.md"))
    assert len(md_files) == 2


def test_export_empty_pages_no_crash(tmp_path):
    """Lege paginalijst geeft geen fout."""
    export([], "markdown", tmp_path)


def test_export_filters_empty_markdown(tmp_path):
    """Pagina's met lege markdown worden niet geëxporteerd."""
    pages = [
        {"url": "https://example.com/a", "title": "A", "markdown": "  "},
        {"url": "https://example.com/b", "title": "B", "markdown": "# B\nContent"},
    ]
    export(pages, "markdown", tmp_path, project_name="filtered")
    content = (tmp_path / "filtered.md").read_text()
    assert "# B" in content


def test_export_invalid_format(tmp_path, two_pages):
    """Onbekend outputformaat gooit ValueError."""
    with pytest.raises(ValueError):
        export(two_pages, "toml", tmp_path)


def test_export_invalid_pages_type(tmp_path):
    """Niet-lijst pages (anders dan swagger dict) gooit TypeError."""
    with pytest.raises(TypeError):
        export("not-a-list", "markdown", tmp_path)  # type: ignore[arg-type]


def test_url_to_filename_simple_path():
    """Eenvoudig pad wordt correct omgezet."""
    assert _url_to_filename("https://docs.example.com/getting-started") == "getting-started"


def test_url_to_filename_nested_path():
    """Genest pad wordt omgezet met underscores."""
    assert _url_to_filename("https://docs.example.com/api/v2/endpoints") == "api_v2_endpoints"


def test_url_to_filename_no_slashes():
    """Resultaat bevat geen schuine strepen."""
    result = _url_to_filename("https://docs.example.com/some/deep/path")
    assert "/" not in result


def test_url_to_filename_root():
    """Root URL geeft 'index' terug."""
    assert _url_to_filename("https://docs.example.com/") == "index"


def test_url_to_filename_max_length():
    """Resultaat is maximaal 100 tekens."""
    long_url = "https://example.com/" + "a" * 200
    result = _url_to_filename(long_url)
    assert len(result) <= 100
