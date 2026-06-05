"""Tests voor de exporter module."""
import pytest

from src.exporter import _spec_basename, _url_to_filename, export


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

    out_file = tmp_path / "md" / "test-docs.md"
    assert out_file.exists()
    content = out_file.read_text()
    assert "Page 1" in content
    assert "Page 2" in content
    assert "docsnap-v2" in content


def test_export_per_file(tmp_path, two_pages):
    """Losse bestanden per pagina worden aangemaakt."""
    export(two_pages, "files", tmp_path)

    files_dir = tmp_path / "md" / "pages"
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
    content = (tmp_path / "md" / "filtered.md").read_text()
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


def test_spec_basename_uses_title_and_version():
    """Bestandsnaam bevat title en version uit de spec."""
    spec = {"openapi": "3.0.3", "info": {"title": "MendriX API", "version": "2025.3.66.7056"}}
    assert _spec_basename(spec, "fallback") == "MendriX-API-2025.3.66.7056-openapi"


def test_spec_basename_title_only():
    """Werkt ook als alleen title aanwezig is."""
    spec = {"info": {"title": "My API"}}
    assert _spec_basename(spec, "fallback") == "My-API-openapi"


def test_spec_basename_fallback_when_no_info():
    """Valt terug op project_name als er geen info-blok is."""
    assert _spec_basename({}, "myproject") == "myproject-openapi"
    assert _spec_basename({"paths": {}}, "myproject") == "myproject-openapi"


def test_export_swagger_json(tmp_path):
    """Swagger JSON spec wordt opgeslagen in json/ submap."""
    spec = {"openapi": "3.0.3", "info": {"title": "Test API", "version": "1.0"}}
    swagger_result = {"format": "json", "spec": spec, "url": "https://example.com/openapi.json"}
    export({"swagger": swagger_result}, "markdown", tmp_path, project_name="test-api")

    out_file = tmp_path / "json" / "Test-API-1.0-openapi.json"
    assert out_file.exists()
    import json
    data = json.loads(out_file.read_text())
    assert data == spec


def test_export_swagger_yaml_raw(tmp_path):
    """Swagger YAML (raw string) wordt opgeslagen in yaml/ submap."""
    raw_yaml = "openapi: 3.0.3\ninfo:\n  title: Test API\n"
    swagger_result = {"format": "yaml_raw", "spec": raw_yaml, "url": "https://example.com/openapi.yaml"}
    export({"swagger": swagger_result}, "markdown", tmp_path, project_name="test-api")

    yaml_dir = tmp_path / "yaml"
    yaml_files = list(yaml_dir.glob("*.yaml"))
    assert len(yaml_files) == 1
