"""
Exporteer gecleande documentatie naar het gewenste outputformaat.

Ondersteunde formaten:
- markdown: één gecombineerd .md bestand
- files:    één .md bestand per pagina (in submap)
- pdf:      één PDF (vereist 'pip install docsnap-v2[pdf]')
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def export(
    pages: list[dict] | dict,
    output_format: str,
    out_dir: Path,
    project_name: str = "documentation",
) -> None:
    """Exporteer documentatie naar het opgegeven formaat.

    Args:
        pages:         Lijst van cleaned page dicts OF swagger result dict.
        output_format: 'markdown', 'files', of 'pdf'.
        out_dir:       Doelmap voor de output.
        project_name:  Naam van het project (voor bestandsnamen).
    """
    if isinstance(pages, dict) and "swagger" in pages:
        _export_swagger(pages["swagger"], out_dir, project_name)
        return

    if not isinstance(pages, list):
        raise TypeError("pages moet een lijst zijn")

    non_empty = [p for p in pages if p.get("markdown", "").strip()]
    if not non_empty:
        print("⚠️  Geen content om te exporteren.")
        return

    if output_format == "markdown":
        _export_combined_markdown(non_empty, out_dir, project_name)
    elif output_format == "files":
        _export_per_file(non_empty, out_dir)
    elif output_format == "pdf":
        _export_pdf(non_empty, out_dir, project_name)
    else:
        raise ValueError(f"Onbekend outputformaat: {output_format}")


def _export_combined_markdown(
    pages: list[dict], out_dir: Path, project_name: str
) -> None:
    """Exporteer alle pagina's als één gecombineerd Markdown bestand."""
    md_dir = out_dir / "md"
    md_dir.mkdir(parents=True, exist_ok=True)
    out_file = md_dir / f"{project_name}.md"
    parts = [f"# {project_name}\n\n_Gegenereerd door docsnap-v2_\n\n---\n\n"]

    for page in pages:
        parts.append(f"## {page.get('title', page['url'])}\n\n")
        parts.append(f"_Bron: {page['url']}_\n\n")
        parts.append(page["markdown"])
        parts.append("\n\n---\n\n")

    out_file.write_text("".join(parts), encoding="utf-8")
    print(f"✅  Opgeslagen: {out_file} ({len(pages)} pagina's)")


def _export_per_file(pages: list[dict], out_dir: Path) -> None:
    """Exporteer elke pagina als apart Markdown bestand."""
    files_dir = out_dir / "md" / "pages"
    files_dir.mkdir(parents=True, exist_ok=True)

    for page in pages:
        filename = _url_to_filename(page["url"])
        out_file = files_dir / f"{filename}.md"
        content = f"# {page.get('title', page['url'])}\n\n"
        content += f"_Bron: {page['url']}_\n\n"
        content += page["markdown"]
        out_file.write_text(content, encoding="utf-8")

    print(f"✅  {len(pages)} bestanden opgeslagen in {files_dir}")


def _export_pdf(pages: list[dict], out_dir: Path, project_name: str) -> None:
    """Exporteer naar PDF via weasyprint."""
    try:
        import weasyprint  # type: ignore[import]  # noqa: F401
    except ImportError:
        print(
            "❌  PDF export vereist weasyprint: pip install 'docsnap-v2[pdf]'\n"
            "   Valt terug op Markdown export."
        )
        _export_combined_markdown(pages, out_dir, project_name)
        return

    # TODO: implementeer volledige HTML → PDF pipeline via weasyprint
    _export_combined_markdown(pages, out_dir, project_name)
    print("⚠️  PDF export nog in ontwikkeling, Markdown opgeslagen.")


def _export_swagger(swagger_result: dict, out_dir: Path, project_name: str) -> None:
    """Sla een Swagger/OpenAPI spec op."""
    fmt = swagger_result.get("format", "json")
    spec = swagger_result.get("spec", {})

    base = _spec_basename(spec, project_name)

    if fmt == "yaml_raw":
        yaml_dir = out_dir / "yaml"
        yaml_dir.mkdir(parents=True, exist_ok=True)
        out_file = yaml_dir / f"{base}.yaml"
        out_file.write_text(str(spec), encoding="utf-8")
    elif fmt == "yaml":
        import yaml  # type: ignore[import]

        yaml_dir = out_dir / "yaml"
        yaml_dir.mkdir(parents=True, exist_ok=True)
        out_file = yaml_dir / f"{base}.yaml"
        out_file.write_text(yaml.dump(spec, allow_unicode=True), encoding="utf-8")
    else:
        import json

        json_dir = out_dir / "json"
        json_dir.mkdir(parents=True, exist_ok=True)
        out_file = json_dir / f"{base}.json"
        out_file.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"✅  OpenAPI spec opgeslagen: {out_file}")


def _spec_basename(spec: dict, fallback: str) -> str:
    """Bouw bestandsnaam op uit spec info.title + info.version."""
    info = spec.get("info", {}) if isinstance(spec, dict) else {}
    title = str(info.get("title", "")).strip()
    version = str(info.get("version", "")).strip()
    if title or version:
        raw = f"{title}-{version}" if (title and version) else (title or version)
        safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in raw)
        safe = safe.strip("-")
        return f"{safe}-openapi"
    return f"{fallback}-openapi"


def _url_to_filename(url: str) -> str:
    """Converteer URL naar een veilige bestandsnaam (max 100 tekens)."""
    parsed = urlparse(url)
    path = parsed.path.strip("/").replace("/", "_") or "index"
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in path)
    return safe[:100]
