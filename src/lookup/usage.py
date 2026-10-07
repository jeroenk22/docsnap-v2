"""Tokenschattingen en de verbruiksteller per Claude Code-chat.

Alles wat Claude van doc-lookup te lezen krijgt kost tokens: de scriptuitvoer
zelf en de documentatie die Claude daarna leest. Elk commando sluit af met een
[tokens]-regel zodat Claude dat aan de gebruiker kan melden.
"""

from __future__ import annotations

import contextlib
import json
import math
import re
from dataclasses import dataclass

import click

from .site import home

CHARS_PER_TOKEN = 3.5  # NL/EN-mix in Markdown; bewust iets pessimistisch


def text_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def image_tokens(width: int, height: int) -> int:
    """Schatting volgens Claude vision: lange zijde max ~1568px, tokens ~ w*h/750."""
    if not width or not height:
        return 1600
    scale = min(1.0, 1568 / max(width, height))
    w, h = width * scale, height * scale
    mp = w * h
    if mp > 1_150_000:  # bovengrens ~1,15 megapixel
        f = math.sqrt(1_150_000 / mp)
        w, h = w * f, h * f
    return max(1, math.ceil(w * h / 750))


def fmt_tokens(n: int) -> str:
    return f"~{n:,}".replace(",", ".") + " tokens"


@dataclass
class Run:
    """Teller voor één commando."""

    out_chars: int = 0  # scriptuitvoer die Claude leest
    doc_tokens: int = 0  # documentatietekst die Claude daarna leest
    img_tokens_max: int = 0  # als Claude alle afbeeldingen bekijkt


RUN = Run()


def say(msg: str = "") -> None:
    """Eén voortgangsregel; telt mee in de tokenschatting."""
    RUN.out_chars += len(msg) + 1
    click.echo(msg)


def warn(msg: str) -> None:
    say(f"  ! {msg}")


def _n(v: int) -> str:
    return f"{v:,}".replace(",", ".")


def session_line(session_id: str | None) -> str:
    """Tel deze stap op bij het chattotaal en geef de [tokens]-regel terug."""
    step = text_tokens("x" * RUN.out_chars) + RUN.doc_tokens
    if not session_id:
        return f"[tokens] deze stap ~{_n(step)}"

    safe = re.sub(r"[^A-Za-z0-9_-]", "", session_id)[:80] or "onbekend"
    path = home() / "sessions" / f"{safe}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    total = {"calls": 0, "tokens": 0, "img_max": 0}
    if path.exists():
        with contextlib.suppress(json.JSONDecodeError):
            total.update(json.loads(path.read_text(encoding="utf-8")))
    total["calls"] += 1
    total["tokens"] += step
    total["img_max"] += RUN.img_tokens_max
    path.write_text(json.dumps(total), encoding="utf-8")

    line = f"[tokens] deze stap ~{_n(step)} | hele chat via doc-lookup ~{_n(total['tokens'])}"
    if total["img_max"]:
        line += f" (+ afbeeldingen alleen als bekeken, max ~{_n(total['img_max'])})"
    return line
