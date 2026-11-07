"""Browser login with automatic token catch.

How it works (no registry change, no copy and paste):
1. Start Chrome with its own profile folder and a local DevTools port (127.0.0.1 only).
2. Open Moodle's mobile-app launch page. Moodle sends the user to the normal UPM login.
3. After the login Moodle answers the launch page with a redirect to
   ``moodlemobile://token=<base64>``. Chrome cannot open that link, but before it tries,
   the DevTools "Fetch" domain pauses the response. We read the token from it and show
   a "done" page instead.
4. The token is checked (signature = md5(site URL + passport)) and goes to keyring.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import websocket

from .client import MoodleError

URL_SCHEME = "moodlemobile"
LAUNCH_PATTERN = "*/admin/tool/mobile/launch.php*"
TOKEN_RE = re.compile(r"moodlemobile://token=([A-Za-z0-9+/=_%-]+)")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DONE_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Moodle login done</title>
<style>body{font-family:system-ui,sans-serif;background:#f6f7f9;color:#1d2433;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}div{background:#fff;padding:32px 40px;
border-radius:12px;box-shadow:0 2px 12px #0001;max-width:420px}h1{font-size:20px;margin:0 0 8px}
p{margin:0;color:#4a5263}</style></head><body><div><h1>Login done</h1>
<p>Claude has the Moodle access now. This window closes by itself.</p></div></body></html>"""


def find_browser() -> str:
    env = os.environ.get("MOODLE_MCP_BROWSER")
    if env and Path(env).exists():
        return env
    cands = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for c in cands:
        if c.exists():
            return str(c)
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        p = shutil.which(name)
        if p:
            return p
    raise MoodleError("No Chrome or Edge browser found.", "install Google Chrome, then run moodle_login again.")


def launch_url(base_url: str, passport: str) -> str:
    q = urllib.parse.urlencode({"service": "moodle_mobile_app", "passport": passport, "urlscheme": URL_SCHEME})
    return f"{base_url}/admin/tool/mobile/launch.php?{q}"


def is_after_login(page_url: str, base_url: str) -> bool:
    """True for a Moodle page that only a logged-in user reaches (not login, SSO or launch pages)."""
    if not page_url.startswith(base_url.rstrip("/") + "/"):
        return False
    path = page_url[len(base_url.rstrip("/")):]
    return not any(path.startswith(x) for x in ("/login/", "/auth/", "/admin/tool/mobile/"))


def parse_token(raw: str, site_urls: list[str], passport: str) -> str:
    """Decode the base64 part of moodlemobile://token=... and check its signature."""
    raw = urllib.parse.unquote(raw)
    if raw.startswith(f"{URL_SCHEME}://token="):
        raw = raw.split("token=", 1)[1]
    raw += "=" * (-len(raw) % 4)
    try:
        text = base64.b64decode(raw).decode("utf-8")
    except Exception:
        raise MoodleError("The login answer from Moodle could not be read.", "run moodle_login again.") from None
    parts = text.split(":::")
    if len(parts) < 2:
        raise MoodleError("The login answer from Moodle has an unknown form.", "run moodle_login again.")
    sig, token = parts[0], parts[1]
    ok = {hashlib.md5((u.rstrip("/") + passport).encode()).hexdigest() for u in site_urls}
    if sig not in ok:
        raise MoodleError("The login answer does not match this login attempt (signature check failed).",
                          "run moodle_login again.")
    return token


class _CDP:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=10, suppress_origin=True)
        self._id = 0
        self.events: list[dict] = []

    def send(self, method: str, params: dict | None = None, session: str | None = None) -> int:
        self._id += 1
        msg: dict = {"id": self._id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        return self._id

    def call(self, method: str, params: dict | None = None, session: str | None = None, timeout: float = 15) -> dict:
        mid = self.send(method, params, session)
        end = time.time() + timeout
        while time.time() < end:
            m = self.recv(max(0.1, end - time.time()))
            if m is None:
                continue
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error'].get('message')}")
                return m.get("result", {})
            if "method" in m:
                self.events.append(m)
        raise TimeoutError(method)

    def recv(self, timeout: float) -> dict | None:
        self.ws.settimeout(timeout)
        try:
            return json.loads(self.ws.recv())
        except websocket.WebSocketTimeoutException:
            return None

    def next_event(self, timeout: float) -> dict | None:
        if self.events:
            return self.events.pop(0)
        m = self.recv(timeout)
        return m if m and "method" in m else None

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def _start_browser(profile: Path, browser: str) -> str:
    """Start (or reuse) the login browser. Returns the browser DevTools websocket URL."""
    profile.mkdir(parents=True, exist_ok=True)
    port_file = profile / "DevToolsActivePort"
    # A login window from an earlier try may still be open: reuse it.
    if port_file.exists():
        try:
            port, path = port_file.read_text().split()[:2]
            ws = websocket.create_connection(f"ws://127.0.0.1:{port}{path}", timeout=3, suppress_origin=True)
            ws.close()
            return f"ws://127.0.0.1:{port}{path}"
        except Exception:
            port_file.unlink(missing_ok=True)
    subprocess.Popen(
        [browser, "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
         f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
         "--new-window", "about:blank"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW,
    )
    end = time.time() + 20
    while time.time() < end:
        if port_file.exists():
            parts = port_file.read_text().split()
            if len(parts) >= 2:
                return f"ws://127.0.0.1:{parts[0]}{parts[1]}"
        time.sleep(0.2)
    raise MoodleError("The login browser did not start.", "close all Chrome login windows and run moodle_login again.")


def _watch(cdp: _CDP, session: str) -> None:
    cdp.call("Fetch.enable", {"patterns": [{"urlPattern": LAUNCH_PATTERN, "requestStage": "Response"}]}, session)


def catch_token(base_url: str, site_urls: list[str], profile: Path, timeout: float = 600,
                browser: str | None = None, on_open=None) -> str:
    """Open the login page and wait for the token. Blocking."""
    passport = secrets.token_hex(8)
    url = launch_url(base_url, passport)
    cdp = _CDP(_start_browser(profile, browser or find_browser()))
    try:
        cdp.call("Target.setDiscoverTargets", {"discover": True})
        attached: dict[str, str] = {}  # targetId -> sessionId

        def attach(target_id: str) -> str | None:
            if target_id in attached:
                return attached[target_id]
            try:
                sid = cdp.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})["sessionId"]
                _watch(cdp, sid)
            except Exception:
                return None
            attached[target_id] = sid
            return sid

        for t in cdp.call("Target.getTargets").get("targetInfos", []):
            if t.get("type") == "page":
                attach(t["targetId"])
        tid = cdp.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        sid = attach(tid)
        cdp.send("Page.bringToFront", {}, sid)
        cdp.send("Page.navigate", {"url": url}, sid)
        if on_open:
            on_open()

        retried: dict[str, float] = {}
        # Pages already open on the site (a login from an earlier try): use them at once.
        for t in cdp.call("Target.getTargets").get("targetInfos", []):
            if t.get("type") == "page" and t["targetId"] != tid and is_after_login(t.get("url", ""), base_url):
                retried[t["targetId"]] = time.time()
                cdp.send("Target.closeTarget", {"targetId": t["targetId"]})
        end = time.time() + timeout
        while time.time() < end:
            ev = cdp.next_event(1.0)
            if not ev:
                continue
            method, p, s = ev["method"], ev.get("params", {}), ev.get("sessionId")
            if method == "Target.targetCreated" and p.get("targetInfo", {}).get("type") == "page":
                attach(p["targetInfo"]["targetId"])
            elif method == "Target.targetInfoChanged" and p.get("targetInfo", {}).get("type") == "page":
                # SSO logins often lose the return address and land on the dashboard.
                # The user is logged in then, so open the launch page again in that tab.
                info = p["targetInfo"]
                if is_after_login(info.get("url", ""), base_url) and time.time() - retried.get(info["targetId"], 0) > 10:
                    retried[info["targetId"]] = time.time()
                    tsid = attach(info["targetId"])
                    if tsid:
                        cdp.send("Page.navigate", {"url": url}, tsid)
            elif method == "Fetch.requestPaused":
                rid = p["requestId"]
                found = None
                headers = {h["name"].lower(): h["value"] for h in p.get("responseHeaders") or []}
                loc = headers.get("location", "")
                m = TOKEN_RE.search(loc)
                if m:
                    found = m.group(1)
                elif p.get("responseStatusCode") == 200:
                    try:
                        body = cdp.call("Fetch.getResponseBody", {"requestId": rid}, s)
                        text = body.get("body", "")
                        if body.get("base64Encoded"):
                            text = base64.b64decode(text).decode("utf-8", "replace")
                        m = TOKEN_RE.search(text)
                        found = m.group(1) if m else None
                    except Exception:
                        found = None
                if found:
                    token = parse_token(found, site_urls, passport)
                    cdp.call("Fetch.fulfillRequest", {
                        "requestId": rid, "responseCode": 200,
                        "responseHeaders": [{"name": "Content-Type", "value": "text/html; charset=utf-8"}],
                        "body": base64.b64encode(DONE_PAGE.encode()).decode()}, s)
                    threading.Thread(target=_close_later, args=(cdp,), daemon=True).start()
                    return token
                cdp.send("Fetch.continueRequest", {"requestId": rid}, s)
        raise MoodleError("No login finished in time.", "run moodle_login again and log in in the window that opens.")
    except BaseException:
        cdp.close()
        raise


def _close_later(cdp: _CDP) -> None:
    time.sleep(4)
    try:
        cdp.send("Browser.close")
        time.sleep(0.5)
    finally:
        cdp.close()


@dataclass
class LoginState:
    status: str = "idle"   # idle | waiting | done | error
    message: str = ""
    started: float = 0.0


class LoginJob:
    """Runs one browser login in a background thread so a tool call can return early."""

    def __init__(self):
        self.state = LoginState()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.opened = threading.Event()

    def start(self, fn) -> bool:
        """fn() does the whole login and returns a message. False if one is already running."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self.state = LoginState("waiting", "Login window open. Waiting for you to log in.", time.time())
            self.opened.clear()

            def run():
                try:
                    msg = fn()
                    self.state = LoginState("done", msg, self.state.started)
                except MoodleError as e:
                    self.state = LoginState("error", str(e), self.state.started)
                except Exception as e:  # noqa: BLE001
                    self.state = LoginState("error", f"Login failed ({type(e).__name__}: {e}). "
                                                     "Next step: run moodle_login again.", self.state.started)
                finally:
                    self.opened.set()

            self._thread = threading.Thread(target=run, daemon=True)
            self._thread.start()
            return True

    def wait(self, seconds: float) -> LoginState:
        if self._thread:
            self._thread.join(seconds)
        return self.state
