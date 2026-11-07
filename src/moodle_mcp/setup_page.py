"""Small local setup page: login button, course checkboxes, folder.

Served on 127.0.0.1 only, under a random path so other web pages cannot call it.
"""
from __future__ import annotations

import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

from . import core
from .client import MoodleError

_server: ThreadingHTTPServer | None = None
_key = secrets.token_urlsafe(16)


def _page() -> bytes:
    return resources.files("moodle_mcp").joinpath("setup.html").read_bytes()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep stdout clean (MCP uses stdio)
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode())

    def _route(self) -> str | None:
        prefix = f"/{_key}"
        if not self.path.startswith(prefix):
            return None
        return self.path[len(prefix):] or "/"

    def do_GET(self):
        r = self._route()
        if r is None:
            return self._send(404, b"not found", "text/plain")
        if r in ("/", "/index.html"):
            return self._send(200, _page(), "text/html; charset=utf-8")
        if r == "/api/state":
            try:
                st = core.status()
                q = core.setup_questions()
                return self._json({"status": st, "questions": q["questions"]})
            except MoodleError as e:
                return self._json({"error": str(e)}, 500)
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        r = self._route()
        if r is None:
            return self._send(404, b"not found", "text/plain")
        # Only same-origin calls: a page on another site cannot set this header without CORS.
        if self.headers.get("X-Moodle-MCP") != "1":
            return self._send(403, b"forbidden", "text/plain")
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            if r == "/api/save":
                return self._json(core.setup_apply(body.get("course_ids"), body.get("master_dir"),
                                                   body.get("unmatched")))
            if r == "/api/login":
                started = core.LOGIN.start(core._login_once)
                return self._json({"started": started, "status": core.LOGIN.state.status})
        except MoodleError as e:
            return self._json({"error": str(e)}, 400)
        return self._send(404, b"not found", "text/plain")


def start(open_browser: bool = True, port: int = 0) -> str:
    global _server
    if _server is None:
        _server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        threading.Thread(target=_server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{_server.server_address[1]}/{_key}/"
    if open_browser:
        webbrowser.open(url)
    return url


def stop() -> None:
    global _server
    if _server:
        _server.shutdown()
        _server.server_close()
        _server = None
