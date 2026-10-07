"""Documentatiebronnen: config, paden en index in ~/.doc-lookup/sites/<naam>/.

Alles staat buiten het project, zodat sessies en cache gedeeld worden tussen
projecten en chats. De map is aan te passen met de omgevingsvariabele
DOC_LOOKUP_HOME.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import yaml

# Subdomeinen die niets zeggen over de bron (docs.example.com -> "example")
COMMON_PREFIXES = {
    "www",
    "docs",
    "doc",
    "support",
    "help",
    "wiki",
    "kb",
    "developer",
    "developers",
    "api",
    "portal",
    "confluence",
    "hc",
}


class SiteNotFound(Exception):
    """De gevraagde bron bestaat (nog) niet."""


def home() -> Path:
    """Basismap van doc-lookup; per aanroep gelezen zodat tests hem kunnen omleiden."""
    return Path(os.environ.get("DOC_LOOKUP_HOME", Path.home() / ".doc-lookup"))


def private_dir(path: Path) -> Path:
    """Maak een map die alleen de eigenaar mag lezen (sessies zijn geheim).

    Op Windows zet os.chmod alleen de alleen-lezen-vlag; daar beschermt het
    gebruikersprofiel de map al. Op macOS/Linux sluit 0o700 andere gebruikers uit.
    """
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def norm_url(url: str) -> str:
    """Canonieke vorm voor cache-sleutels: zonder fragment en trailing slash."""
    p = urlparse(url.strip())
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", p.query, ""))


def slugify(text: str, maxlen: int = 80) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return (s or "pagina")[:maxlen].rstrip("-")


def url_slug(url: str) -> str:
    """Leesbare, unieke bestandsnaam voor een pagina-URL."""
    p = urlparse(url)
    base = slugify((p.path + ("-" + p.query if p.query else "")) or "index", 70)
    return f"{base}-{sha(norm_url(url))[:6]}"


def default_name(url: str) -> str:
    """Korte bronnaam uit de URL: https://support.mendrix.nl/... -> 'mendrix'."""
    parts = [p for p in urlparse(url).netloc.split(":")[0].split(".") if p]
    core = [p for p in parts[:-1] if p not in COMMON_PREFIXES] or parts[:1]
    name = core[-1].lower() if core else "docs"
    return "docs" if name.isdigit() else name


def list_sites() -> list[str]:
    sites = home() / "sites"
    if not sites.exists():
        return []
    return sorted(p.name for p in sites.iterdir() if (p / "site.yaml").exists())


def load_env_file() -> dict[str, str]:
    """Optionele inloggegevens uit ~/.doc-lookup/.env (nooit in chat of repo).

    Omgevingsvariabelen met prefix DOC_LOOKUP_ gaan voor.
    """
    env: dict[str, str] = {}
    path = home() / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith("DOC_LOOKUP_")})
    return env


class Site:
    """Een documentatiebron met eigen map, config, sessie en paginacache."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.dir = home() / "sites" / name
        self.cfg_path = self.dir / "site.yaml"
        self.auth_path = self.dir / "auth" / "state.json"
        self.pages_dir = self.dir / "pages"
        self.images_dir = self.dir / "images"
        self.debug_dir = self.dir / "debug"
        self.index_path = self.dir / "index.json"
        self.usage_path = self.dir / "usage.jsonl"
        self.last_search_path = self.dir / "last_search.json"
        self.cfg: dict = {}
        self.index: dict = {}

    @classmethod
    def load(cls, name: str) -> Site:
        site = cls(name)
        if not site.cfg_path.exists():
            known = ", ".join(list_sites()) or "(geen)"
            raise SiteNotFound(
                f"Bron '{name}' bestaat nog niet. Bekende bronnen: {known}. "
                f"Maak hem aan met: init <url> --name {name}"
            )
        site.cfg = yaml.safe_load(site.cfg_path.read_text(encoding="utf-8")) or {}
        if site.index_path.exists():
            site.index = json.loads(site.index_path.read_text(encoding="utf-8"))
        return site

    def ensure_dirs(self) -> None:
        for d in (self.dir, self.pages_dir, self.images_dir, self.debug_dir):
            d.mkdir(parents=True, exist_ok=True)
        private_dir(home())
        private_dir(self.auth_path.parent)

    def save_cfg(self) -> None:
        self.ensure_dirs()
        self.cfg_path.write_text(
            yaml.safe_dump(self.cfg, sort_keys=False, allow_unicode=True, width=120),
            encoding="utf-8",
        )

    def save_index(self) -> None:
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.index, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(self.index_path)

    def get(self, dotted: str, default=None):
        """Lees een geneste config-waarde: site.get('content.selector')."""
        cur = self.cfg
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    @property
    def base_url(self) -> str:
        return self.cfg.get("base_url", "")

    def has_auth(self) -> bool:
        return self.auth_path.exists()

    def log(self, path: Path, record: dict) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": now_iso(), **record}, ensure_ascii=False) + "\n")
