"""A fake Moodle: the web-service functions and files the server uses, plus the mobile launch page."""
from __future__ import annotations

import base64
import hashlib
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "fake-token-123"

COURSES = [
    {"id": 2702, "fullname": "05AZ. MII - Sistemas Integrados de Fabricación", "shortname": "SIF"},
    {"id": 736, "fullname": "Dirección financiera", "shortname": "DF"},
    {"id": 3692, "fullname": "DSE", "shortname": "DSE"},
]

FILES = {
    "/1/mod_resource/content/1/Tema1.pdf": b"%PDF tema 1",
    "/2/mod_folder/content/0/sub/Datos.xlsx": b"xlsx data",
    "/2/mod_folder/content/0/Guia.pdf": b"%PDF guia",
    "/3/mod_assign/introattachment/0/Enunciado.pdf": b"%PDF enunciado",
    "/4/mod_resource/content/1/DF1-Contabilidad.pdf": b"%PDF df1",
}


class FakeMoodle:
    def __init__(self):
        self.calls: list[str] = []
        self.downloads: list[str] = []
        self.timemodified = 1_700_000_000
        self.extra_file = False
        self.lose_return = False  # SSO that lands on /my/ the first time
        self.launch_visits = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _file(self, path: str, name: str, filepath: str = "/") -> dict:
        return {"type": "file", "filename": name, "filepath": filepath, "filesize": len(FILES[path]),
                "fileurl": f"{self.url}/webservice/pluginfile.php{path}?forcedownload=1",
                "timemodified": self.timemodified}

    def contents(self, cid: int) -> list:
        if cid == 2702:
            mods = [
                {"id": 1, "name": "Tema 1 slides", "modname": "resource",
                 "contents": [self._file("/1/mod_resource/content/1/Tema1.pdf", "Tema1.pdf")]},
                {"id": 2, "name": "Material: extra", "modname": "folder",
                 "contents": [self._file("/2/mod_folder/content/0/Guia.pdf", "Guia.pdf"),
                              self._file("/2/mod_folder/content/0/sub/Datos.xlsx", "Datos.xlsx", "/sub/")]},
                {"id": 9, "name": "Web", "modname": "url",
                 "contents": [{"type": "url", "fileurl": "https://example.org"}]},
            ]
            if self.extra_file:
                FILES["/5/mod_resource/content/1/Tema2.pdf"] = b"%PDF tema 2"
                mods.append({"id": 5, "name": "Tema 2", "modname": "resource",
                             "contents": [self._file("/5/mod_resource/content/1/Tema2.pdf", "Tema2.pdf")]})
            return [{"id": 10, "name": "Tema 1: Sistemas", "modules": mods}]
        if cid == 736:
            return [{"id": 20, "name": "General", "modules": [
                {"id": 4, "name": "DF1", "modname": "resource",
                 "contents": [self._file("/4/mod_resource/content/1/DF1-Contabilidad.pdf", "DF1-Contabilidad.pdf")]}]}]
        return [{"id": 30, "name": "General", "modules": []}]

    def ws(self, fn: str, params: dict) -> object:
        self.calls.append(fn)
        if params.get("wstoken") != TOKEN:
            return {"exception": "moodle_exception", "errorcode": "invalidtoken", "message": "Invalid token"}
        if fn == "core_webservice_get_site_info":
            return {"userid": 7, "fullname": "Test Student", "sitename": "Fake Moodle", "downloadfiles": 1,
                    "functions": [{"name": n} for n in ("core_enrol_get_users_courses", "core_course_get_contents",
                                                         "mod_assign_get_assignments")]}
        if fn == "core_enrol_get_users_courses":
            return COURSES
        if fn == "core_course_get_contents":
            return self.contents(int(params["courseid"]))
        if fn == "mod_assign_get_assignments":
            ids = {int(v) for k, v in params.items() if k.startswith("courseids[")}
            out = []
            if 2702 in ids:
                out.append({"id": 2702, "fullname": COURSES[0]["fullname"], "assignments": [
                    {"id": 1, "cmid": 3, "name": "Práctica 1", "duedate": 4_102_444_800, "cutoffdate": 0,
                     "allowsubmissionsfromdate": 0,
                     "introattachments": [self._file("/3/mod_assign/introattachment/0/Enunciado.pdf", "Enunciado.pdf")]},
                    {"id": 2, "cmid": 4, "name": "Old task", "duedate": 1_000_000_000, "cutoffdate": 0,
                     "allowsubmissionsfromdate": 0, "introattachments": []},
                ]})
            return {"courses": out, "warnings": []}
        return {"exception": "webservice_access_exception", "errorcode": "accessexception", "message": "no"}

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body: bytes, ctype="application/json", headers=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                params = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
                if self.path.startswith("/webservice/rest/server.php"):
                    return self._send(200, json.dumps(fake.ws(params.get("wsfunction", ""), params)).encode())
                self._send(404, b"{}")

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                if u.path.startswith("/webservice/pluginfile.php"):
                    if q.get("token") != TOKEN:
                        return self._send(403, json.dumps({"error": "bad token", "errorcode": "invalidtoken"}).encode())
                    path = u.path[len("/webservice/pluginfile.php"):]
                    fake.downloads.append(path)
                    return self._send(200, FILES[path], "application/octet-stream")
                if u.path == "/my/":
                    return self._send(200, b"<html><title>Dashboard</title></html>", "text/html")
                if u.path == "/admin/tool/mobile/launch.php":
                    fake.launch_visits += 1
                    if fake.lose_return and fake.launch_visits == 1:
                        return self._send(303, b"", "text/html", {"Location": f"{fake.url}/my/"})
                    sig = hashlib.md5((fake.url + q["passport"]).encode()).hexdigest()
                    raw = base64.b64encode(f"{sig}:::{TOKEN}:::private".encode()).decode()
                    return self._send(303, b"", "text/html", {"Location": f"moodlemobile://token={raw}"})
                self._send(404, b"not found", "text/plain")

        return H
