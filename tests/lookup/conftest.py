"""Fixtures voor doc-lookup: eigen DOC_LOOKUP_HOME en een lokale documentatiesite.

De testsite heeft een cookie-login (jeroen/geheim), een pagina waarvan de
content pas na een seconde via JavaScript verschijnt (zoals veel SPA's), een
afbeelding die alleen ingelogd op te halen is, en een 404.
"""

from __future__ import annotations

import os
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

pytest.importorskip("bs4", reason="vereist de extra 'lookup'")


def _png(w: int, h: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + bytes((200, 30, 30)) * w for _ in range(h))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


LOGIN_PAGE = (
    "<html><body><h1>Inloggen</h1><form method=post action=/login>"
    "<input name=user><input type=password name=pass>"
    "<button type=submit>Log in</button></form></body></html>"
)

LATE_CONTENT = (
    "<main class='article-body'><h1>Gebeurtenis instellen</h1>"
    "<p>Met een <strong>gebeurtenis</strong> leg je vast wat er met een order gebeurt.</p>"
    "<div class='confluence-information-macro confluence-information-macro-warning'>"
    "<p>Gebeurtenissen kunnen niet worden verwijderd na verzending.</p></div>"
    "<h2>Velden</h2><table><tr><th>Veld</th><th>Betekenis</th></tr>"
    "<tr><td>Code</td><td>Unieke code (max 10 tekens)</td></tr></table>"
    "<img src='/img/scherm.png' alt='Scherm gebeurtenissen'>"
    "<pre><code class='language-sql'>SELECT * FROM events;</code></pre></main>"
)

LATE_PAGE = (
    "<html><head><title>Gebeurtenis</title></head><body><nav>menu</nav>"
    "<div id=app><div class=spinner>Laden...</div></div><script>"
    "setTimeout(() => { document.getElementById('app').innerHTML = "
    f'"{LATE_CONTENT}"; }}, 1000);</script></body></html>'
)

STATIC_PAGE = (
    "<html><head><title>Ritten</title></head><body><nav>menu</nav>"
    "<main class='article-body'><h1>Ritten plannen</h1><p>"
    + "Ritten plan je in het planbord. " * 10
    + "</p></main></body></html>"
)

EMPTY_PAGE = "<html><body><main class='article-body'></main></body></html>"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        pass

    def _send(self, code: int, body: bytes | str, ctype: str = "text/html") -> None:
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str, cookie: str | None = None) -> None:
        self.send_response(302)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Location", location)
        self.end_headers()

    def do_POST(self) -> None:
        n = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(n).decode())
        if form.get("user") == ["jeroen"] and form.get("pass") == ["geheim"]:
            return self._redirect("/docs/static", "session=ok; Path=/")
        return self._send(200, LOGIN_PAGE)

    def do_GET(self) -> None:
        if self.path == "/login":
            return self._send(200, LOGIN_PAGE)
        if self.path.startswith("/open/"):
            return self._send(200, STATIC_PAGE)
        if "session=ok" not in (self.headers.get("Cookie") or ""):
            return self._redirect("/login")
        pages = {
            "/docs/late": LATE_PAGE,
            "/docs/static": STATIC_PAGE,
            "/docs/empty": EMPTY_PAGE,
        }
        if self.path in pages:
            return self._send(200, pages[self.path])
        if self.path == "/img/scherm.png":
            return self._send(200, _png(640, 360), "image/png")
        return self._send(404, "niet gevonden")


@pytest.fixture(scope="session")
def docsite() -> str:
    """Basis-URL van de lokale testsite."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def lookup_home(tmp_path, monkeypatch):
    """Elke test een eigen, lege ~/.doc-lookup."""
    home = tmp_path / "dlhome"
    monkeypatch.setenv("DOC_LOOKUP_HOME", str(home))
    for k in list(os.environ):
        if k.startswith("DOC_LOOKUP_") and k != "DOC_LOOKUP_HOME":
            monkeypatch.delenv(k)
    return home
