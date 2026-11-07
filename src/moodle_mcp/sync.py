"""Course contents, downloads (only new files) and assignments."""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .client import MoodleClient, MoodleError
from .config import Settings, course_folder, home, safe_name

ASSIGN_FOLDER = "Tareas"


def _long(p: Path) -> str:
    """Windows path that may be longer than 260 characters."""
    s = str(p.resolve())
    if os.name == "nt" and not s.startswith("\\\\?\\") and len(s) > 240:
        return "\\\\?\\" + s
    return s


def _strip_token(url: str) -> str:
    return re.sub(r"([?&])token=[^&]*&?", r"\1", url).rstrip("?&")


class Manifest:
    """What was downloaded before: course id -> file key -> {timemodified, filesize, path}."""

    def __init__(self, path: Path | None = None):
        self.path = path or home() / "downloads.json"
        self.data: dict[str, dict[str, dict]] = {}
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def is_current(self, course_id: int, key: str, f: dict, dest: Path) -> bool:
        e = self.data.get(str(course_id), {}).get(key)
        return bool(e and e.get("timemodified") == f.get("timemodified")
                    and e.get("filesize") == f.get("filesize") and os.path.exists(_long(dest)))

    def mark(self, course_id: int, key: str, f: dict, dest: Path) -> None:
        self.data.setdefault(str(course_id), {})[key] = {
            "timemodified": f.get("timemodified"), "filesize": f.get("filesize"), "path": str(dest)}

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")


def _fmt_time(ts: int | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def course_files(client: MoodleClient, course_id: int) -> list[tuple[Path, dict]]:
    """(relative path, file dict) for every downloadable file in the course."""
    out: list[tuple[Path, dict]] = []
    used: set[str] = set()
    for i, sec in enumerate(client.contents(course_id)):
        sec_name = safe_name(f"{i:02d} {sec.get('name') or 'Sección'}", 60)
        for mod in sec.get("modules") or []:
            files = [c for c in mod.get("contents") or [] if c.get("type") == "file" and c.get("fileurl")]
            if not files:
                continue
            mod_dir = Path(sec_name)
            # A one-file resource goes straight into the section; folders keep their own folder.
            if mod.get("modname") != "resource" or len(files) > 1:
                mod_dir = mod_dir / safe_name(mod.get("name") or "Recurso", 60)
            for f in files:
                sub = [safe_name(x, 60) for x in (f.get("filepath") or "/").strip("/").split("/") if x]
                rel = mod_dir.joinpath(*sub, safe_name(f.get("filename") or "file"))
                key = str(rel).lower()
                n = 2
                while key in used:  # two files with the same name in one place
                    rel = rel.with_name(f"{rel.stem} ({n}){rel.suffix}")
                    key = str(rel).lower()
                    n += 1
                used.add(key)
                out.append((rel, f))
    return out


def assignment_files(client: MoodleClient, course_id: int) -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    try:
        courses = client.assignments([course_id])
    except MoodleError:
        return out
    for c in courses:
        for a in c.get("assignments") or []:
            for f in (a.get("introattachments") or []) + (a.get("introfiles") or []):
                if f.get("fileurl"):
                    rel = Path(ASSIGN_FOLDER) / safe_name(a.get("name") or "Tarea", 60) / safe_name(f.get("filename", "file"))
                    out.append((rel, f))
    # Same file can be in both lists.
    seen, uniq = set(), []
    for rel, f in out:
        if str(rel).lower() not in seen:
            seen.add(str(rel).lower())
            uniq.append((rel, f))
    return uniq


def download_course(client: MoodleClient, settings: Settings, course: dict, manifest: Manifest,
                    only_new: bool = True) -> dict:
    cid, cname = int(course["id"]), course.get("fullname") or course.get("shortname") or str(course["id"])
    dest_root = course_folder(settings, cid, cname)
    if dest_root is None:
        return {"course_id": cid, "course": cname, "skipped": "course not in moodle-watch.yaml (setup says skip)"}
    result = {"course_id": cid, "course": cname, "folder": str(dest_root), "new": [], "updated": [],
              "unchanged": 0, "errors": []}
    for rel, f in course_files(client, cid) + assignment_files(client, cid):
        dest = dest_root / rel
        key = _strip_token(f["fileurl"])
        if only_new and manifest.is_current(cid, key, f, dest):
            result["unchanged"] += 1
            continue
        existed = os.path.exists(_long(dest))
        tmp = dest.with_name(dest.name + ".part")
        try:
            os.makedirs(_long(dest.parent), exist_ok=True)
            client.download(f["fileurl"], _long(tmp))
            os.replace(_long(tmp), _long(dest))
            tm = f.get("timemodified")
            if tm:
                os.utime(_long(dest), (time.time(), tm))
        except MoodleError as e:
            result["errors"].append(f"{rel}: {e.message}")
            if e.code in {"invalidtoken", "invalidtokensession"}:
                raise
            continue
        except OSError as e:
            result["errors"].append(f"{rel}: cannot write the file ({e.strerror}).")
            continue
        finally:
            if os.path.exists(_long(tmp)):
                os.remove(_long(tmp))
        manifest.mark(cid, key, f, dest)
        (result["updated"] if existed else result["new"]).append(str(rel))
        manifest.save()
    manifest.save()
    return result


def contents_summary(client: MoodleClient, course_id: int) -> list[dict]:
    out = []
    for sec in client.contents(course_id):
        mods = []
        for m in sec.get("modules") or []:
            item = {"name": m.get("name"), "type": m.get("modname")}
            files = [c for c in m.get("contents") or [] if c.get("type") == "file"]
            if files:
                item["files"] = [{"name": c.get("filename"), "size": c.get("filesize"),
                                  "modified": _fmt_time(c.get("timemodified"))} for c in files]
            urls = [c.get("fileurl") for c in m.get("contents") or [] if c.get("type") == "url"]
            if urls:
                item["links"] = urls
            if m.get("dates"):
                item["dates"] = {d.get("label"): _fmt_time(d.get("timestamp")) for d in m["dates"]}
            mods.append(item)
        out.append({"section": sec.get("name"), "modules": mods})
    return out


def assignments_list(client: MoodleClient, course_ids: list[int], due_after: int | None) -> list[dict]:
    out = []
    for c in client.assignments(course_ids):
        for a in c.get("assignments") or []:
            due = a.get("duedate") or 0
            if due_after is not None and due and due < due_after:
                continue
            out.append({
                "course_id": c.get("id"), "course": c.get("fullname"), "name": a.get("name"),
                "due": _fmt_time(due) or "no due date", "due_ts": due,
                "cutoff": _fmt_time(a.get("cutoffdate")), "opens": _fmt_time(a.get("allowsubmissionsfromdate")),
                "assign_id": a.get("id"), "cmid": a.get("cmid"),
            })
    out.sort(key=lambda x: (x["due_ts"] == 0, x["due_ts"]))
    return out


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())
