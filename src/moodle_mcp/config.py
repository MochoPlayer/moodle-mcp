"""Settings, paths and the course map (moodle-watch.yaml).

Nothing secret is stored here. The token lives in Windows Credential Manager
(see tokenstore.py).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

DEFAULT_BASE_URL = "https://moodle.upm.es/titulaciones/oficiales"
DEFAULT_MASTER_DIR = r"C:\Users\mcast\OneDrive - Universidad Politécnica de Madrid\Master"
WATCH_FILE = "moodle-watch.yaml"
RANDOM_FOLDER = "Random"


def home() -> Path:
    """Folder for settings, download index and the login browser profile."""
    env = os.environ.get("MOODLE_MCP_HOME")
    if env:
        p = Path(env)
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        p = Path(base) / "moodle-mcp"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Settings:
    base_url: str = DEFAULT_BASE_URL
    master_dir: str = DEFAULT_MASTER_DIR
    # Course ids to download. Empty list + setup_done False = not set up yet.
    course_ids: list[int] = field(default_factory=list)
    # What to do with courses that are not in moodle-watch.yaml: "random" or "skip".
    unmatched: str = "random"
    setup_done: bool = False

    @classmethod
    def load(cls) -> "Settings":
        f = home() / "settings.json"
        s = cls()
        if os.environ.get("MOODLE_MCP_BASE_URL"):
            s.base_url = os.environ["MOODLE_MCP_BASE_URL"]
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(s, k):
                    setattr(s, k, v)
        if os.environ.get("MOODLE_MCP_BASE_URL"):
            s.base_url = os.environ["MOODLE_MCP_BASE_URL"]
        if os.environ.get("MOODLE_MCP_MASTER_DIR"):
            s.master_dir = os.environ["MOODLE_MCP_MASTER_DIR"]
        s.base_url = s.base_url.rstrip("/")
        return s

    def save(self) -> None:
        f = home() / "settings.json"
        f.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")


@dataclass
class Subject:
    course_id: int
    name: str            # subject name in the yaml
    folder: str          # subject folder under Master
    course_name: str     # Moodle course name in the yaml
    primary: bool        # False for a related course shell (INGENIA has two)


def load_course_map(master_dir: str) -> dict[int, Subject]:
    """course_id -> Subject, from Master/moodle-watch.yaml. Empty if the file is missing."""
    f = Path(master_dir) / WATCH_FILE
    if not f.exists():
        return {}
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    out: dict[int, Subject] = {}
    for subj in data.get("subjects") or []:
        m = subj.get("moodle") or {}
        if m.get("course_id"):
            out[int(m["course_id"])] = Subject(int(m["course_id"]), subj.get("name", ""),
                                               subj.get("folder", ""), m.get("course_name", ""), True)
        if m.get("related_course_id"):
            out[int(m["related_course_id"])] = Subject(int(m["related_course_id"]), subj.get("name", ""),
                                                       subj.get("folder", ""),
                                                       m.get("related_course_name", ""), False)
    return out


def load_unmatched(master_dir: str) -> dict[int, str]:
    f = Path(master_dir) / WATCH_FILE
    if not f.exists():
        return {}
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return {int(c["course_id"]): c.get("course_name", "") for c in data.get("unmatched_moodle_courses") or []}


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, limit: int = 80) -> str:
    """A file or folder name Windows accepts."""
    name = _BAD.sub("_", name or "").strip().rstrip(". ")
    if not name:
        name = "_"
    if name.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                      *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    if len(name) > limit:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) <= 8:
            name = stem[: limit - len(ext) - 1].rstrip(". ") + "." + ext
        else:
            name = name[:limit].rstrip(". ")
    return name


def course_folder(settings: Settings, course_id: int, course_name: str) -> Path | None:
    """Where a course's files go. None = skip this course."""
    cmap = load_course_map(settings.master_dir)
    master = Path(settings.master_dir)
    subj = cmap.get(course_id)
    if subj and subj.folder:
        base = master / subj.folder / "Moodle"
        return base if subj.primary else base / safe_name(subj.course_name or course_name)
    if settings.unmatched == "skip":
        return None
    return master / RANDOM_FOLDER / "Moodle" / safe_name(course_name or str(course_id))
