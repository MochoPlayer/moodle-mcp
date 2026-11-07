"""The actions behind the MCP tools and the setup page."""
from __future__ import annotations

import unicodedata
from datetime import datetime
from pathlib import Path

from . import tokenstore
from .client import MoodleClient, MoodleError
from .config import Settings, course_folder, home, load_course_map, load_unmatched
from .login import LoginJob, catch_token
from .sync import Manifest, assignments_list, contents_summary, download_course, now_ts

LOGIN = LoginJob()


def settings() -> Settings:
    return Settings.load()


def client(s: Settings | None = None) -> MoodleClient:
    s = s or settings()
    token = tokenstore.get_token(s.base_url)
    if not token:
        raise MoodleError("Not logged in to Moodle yet.", "run moodle_login; a browser window opens for the UPM login.")
    return MoodleClient(s.base_url, token)


# --- login ---------------------------------------------------------------
def _login_once() -> str:
    s = settings()
    site_urls = [s.base_url]
    token = catch_token(s.base_url, site_urls, home() / "login-browser", timeout=900,
                        on_open=lambda: LOGIN.opened.set())
    info = MoodleClient(s.base_url, token).site_info()
    tokenstore.set_token(s.base_url, token)
    return f"Logged in as {info.get('fullname')} on {info.get('sitename')}."


def login(wait_seconds: float = 240) -> dict:
    started = LOGIN.start(_login_once)
    LOGIN.opened.wait(30)
    st = LOGIN.wait(wait_seconds)
    out = {"status": st.status, "message": st.message}
    if st.status == "waiting":
        out["next_step"] = ("The login window is open. Log in there. Then call moodle_status "
                            "(the wait goes on in the background for 15 minutes).")
    if not started:
        out["note"] = "A login was already running; this call waited for it."
    return out


def logout() -> dict:
    s = settings()
    tokenstore.delete_token(s.base_url)
    return {"status": "logged out", "message": "The token is removed from Windows Credential Manager."}


# --- status and setup ----------------------------------------------------
def status() -> dict:
    s = settings()
    out: dict = {"site": s.base_url, "master_dir": s.master_dir, "setup_done": s.setup_done, "unmatched": s.unmatched,
                 "login": LOGIN.state.status if LOGIN.state.status != "idle" else None}
    if LOGIN.state.status in {"waiting", "error"}:
        out["login_message"] = LOGIN.state.message
    token = tokenstore.get_token(s.base_url)
    if not token:
        out["logged_in"] = False
        out["next_step"] = ("Wait: log in in the open browser window." if LOGIN.state.status == "waiting"
                            else "run moodle_login; a browser window opens for the UPM login.")
        return out
    try:
        info = MoodleClient(s.base_url, token).site_info()
        out.update(logged_in=True, token_valid=True, user=info.get("fullname"))
        names = {f["name"] for f in info.get("functions", [])}
        need = ["core_enrol_get_users_courses", "core_course_get_contents", "mod_assign_get_assignments"]
        missing = [n for n in need if n not in names]
        if missing:
            out["missing_functions"] = missing
        out["file_download"] = bool(info.get("downloadfiles"))
    except MoodleError as e:
        out.update(logged_in=True, token_valid=False, error=e.message, next_step=e.next_step)
        return out
    if not s.setup_done:
        out["next_step"] = "run moodle_setup_questions and ask the owner, or moodle_open_setup_page."
    else:
        out["courses_selected"] = len(s.course_ids)
        out["next_step"] = "ready: list_courses, get_course_contents, download, list_assignments."
    return out


def _known_courses(s: Settings) -> list[dict]:
    """Enrolled courses if logged in, else the yaml list."""
    cmap = load_course_map(s.master_dir)
    try:
        enrolled = client(s).courses()
        rows = [{"id": int(c["id"]), "name": c.get("fullname"), "in_yaml": int(c["id"]) in cmap} for c in enrolled]
    except MoodleError:
        rows = [{"id": cid, "name": sub.course_name, "in_yaml": True} for cid, sub in cmap.items()]
        rows += [{"id": cid, "name": n, "in_yaml": False} for cid, n in load_unmatched(s.master_dir).items()]
    for r in rows:
        sub = cmap.get(r["id"])
        r["subject"] = sub.name if sub else None
        folder = course_folder(Settings(**{**s.__dict__, "unmatched": "random"}), r["id"], r["name"] or "")
        r["folder"] = str(folder) if folder else None
    rows.sort(key=lambda r: (not r["in_yaml"], (r["name"] or "").lower()))
    return rows


def setup_questions() -> dict:
    s = settings()
    courses = _known_courses(s)
    selected = set(s.course_ids) if s.setup_done else {c["id"] for c in courses if c["in_yaml"]}
    return {
        "how_to_ask": ("Ask the owner these questions (AskUserQuestion, recommended option first), then call "
                       "moodle_setup_apply with the answers."),
        "questions": [
            {"key": "course_ids", "question": "Which Moodle courses should Claude download?",
             "multi_select": True,
             "options": [{"value": c["id"], "label": c["name"], "subject": c["subject"],
                          "recommended": c["id"] in selected} for c in courses],
             "recommended": sorted(selected)},
            {"key": "master_dir", "question": "Where is the Master folder (files go to <subject>\\Moodle\\)?",
             "options": [{"value": s.master_dir, "label": "Keep this folder", "recommended": True}]},
            {"key": "unmatched", "question": "Courses with no subject folder in moodle-watch.yaml: what to do?",
             "options": [{"value": "random", "label": "Put them in Master\\Random\\Moodle\\<course>", "recommended": True},
                         {"value": "skip", "label": "Do not download them"}]},
        ],
    }


def setup_apply(course_ids: list[int] | None = None, master_dir: str | None = None,
                unmatched: str | None = None) -> dict:
    s = settings()
    if course_ids is not None:
        s.course_ids = sorted({int(c) for c in course_ids})
    if master_dir:
        if not Path(master_dir).is_dir():
            raise MoodleError(f"The folder {master_dir} does not exist.", "give an existing folder.")
        s.master_dir = master_dir
    if unmatched:
        if unmatched not in {"random", "skip"}:
            raise MoodleError("unmatched must be 'random' or 'skip'.", "call moodle_setup_apply again.")
        s.unmatched = unmatched
    s.setup_done = True
    s.save()
    return {"saved": True, "course_ids": s.course_ids, "master_dir": s.master_dir, "unmatched": s.unmatched}


# --- data ----------------------------------------------------------------
def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return " ".join(t.split())


def resolve_course(c: MoodleClient, s: Settings, query: str | int) -> dict:
    courses = c.courses()
    q = str(query).strip()
    if q.isdigit():
        for co in courses:
            if int(co["id"]) == int(q):
                return co
    cmap = load_course_map(s.master_dir)
    nq = _norm(q)
    hits = []
    for co in courses:
        sub = cmap.get(int(co["id"]))
        texts = [co.get("fullname", ""), co.get("shortname", "")]
        if sub:
            texts += [sub.name, sub.folder]
        if any(nq in _norm(t) for t in texts if t):
            hits.append(co)
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise MoodleError(f"No enrolled course matches '{query}'.", "call list_courses and use the course id.")
    names = ", ".join(f"{h['id']} {h.get('fullname')}" for h in hits[:8])
    raise MoodleError(f"'{query}' matches more than one course: {names}.", "call again with the course id.")


def list_courses() -> list[dict]:
    s = settings()
    c = client(s)
    cmap = load_course_map(s.master_dir)
    out = []
    for co in c.courses():
        cid = int(co["id"])
        sub = cmap.get(cid)
        folder = course_folder(s, cid, co.get("fullname") or "")
        out.append({"id": cid, "name": co.get("fullname"), "short": co.get("shortname"),
                    "subject": sub.name if sub else None,
                    "selected": cid in s.course_ids if s.setup_done else bool(sub),
                    "folder": str(folder) if folder else None})
    out.sort(key=lambda r: (r["subject"] is None, (r["name"] or "").lower()))
    return out


def get_course_contents(course: str | int) -> dict:
    s = settings()
    c = client(s)
    co = resolve_course(c, s, course)
    return {"course_id": co["id"], "course": co.get("fullname"), "sections": contents_summary(c, int(co["id"]))}


def download(course: str | int = "all", only_new: bool = True) -> dict:
    s = settings()
    c = client(s)
    if str(course).strip().lower() == "all":
        if not s.setup_done:
            raise MoodleError("Setup is not done, so Claude does not know which courses to download.",
                              "run moodle_setup_questions (or moodle_open_setup_page) first.")
        wanted = set(s.course_ids)
        targets = [co for co in c.courses() if int(co["id"]) in wanted]
    else:
        targets = [resolve_course(c, s, course)]
    manifest = Manifest()
    results = [download_course(c, s, co, manifest, only_new) for co in targets]
    total_new = sum(len(r.get("new", [])) + len(r.get("updated", [])) for r in results)
    return {"downloaded": total_new, "courses": results,
            "summary": f"{total_new} new or changed files in {len(results)} course(s)."}


def list_assignments(course: str | int = "all", due_after: str | None = "today") -> dict:
    s = settings()
    c = client(s)
    if str(course).strip().lower() == "all":
        ids = s.course_ids if s.setup_done and s.course_ids else [int(co["id"]) for co in c.courses()]
    else:
        ids = [int(resolve_course(c, s, course)["id"])]
    after: int | None
    if due_after in (None, "", "all"):
        after = None
    elif due_after == "today":
        d = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        after = int(d.timestamp())
    else:
        try:
            after = int(datetime.fromisoformat(due_after).timestamp())
        except ValueError:
            raise MoodleError(f"'{due_after}' is not a date.", "use YYYY-MM-DD, 'today' or 'all'.") from None
    items = assignments_list(c, ids, after)
    for i in items:
        i.pop("due_ts", None)
    return {"count": len(items), "assignments": items, "checked_at": datetime.fromtimestamp(now_ts()).isoformat()}
