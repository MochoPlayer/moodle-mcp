"""MCP server: UPM Moodle for Claude (login, courses, contents, downloads, assignments)."""
from __future__ import annotations

import json
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer

from . import core, setup_page
from .client import MoodleError

INSTRUCTIONS = """UPM Moodle tools. Start with moodle_status: it says the next step in plain words.
Not logged in -> moodle_login (opens a browser window; the owner logs in; the token is caught by itself).
Setup not done -> moodle_setup_questions, ask the owner with AskUserQuestion, then moodle_setup_apply
(or moodle_open_setup_page for the owner's own page). Never ask the owner to type commands."""

mcp = MCPServer("moodle", instructions=INSTRUCTIONS)


def _safe(fn: Callable[..., Any], *a, **kw) -> str:
    try:
        return json.dumps(fn(*a, **kw), ensure_ascii=False, indent=1)
    except MoodleError as e:
        return json.dumps({"error": e.message, "next_step": e.next_step}, ensure_ascii=False)


@mcp.tool()
def moodle_status() -> str:
    """Is Moodle ready? Shows login, token check, setup state and the next step in plain words."""
    return _safe(core.status)


@mcp.tool()
def moodle_login(wait_seconds: int = 240) -> str:
    """Open the UPM Moodle login in a browser window and catch the token by itself when the owner logs in.
    Waits up to wait_seconds; the wait goes on in the background (15 min), so check with moodle_status."""
    return _safe(core.login, max(5, min(wait_seconds, 600)))


@mcp.tool()
def moodle_logout() -> str:
    """Remove the stored Moodle token from Windows Credential Manager."""
    return _safe(core.logout)


@mcp.tool()
def moodle_setup_questions() -> str:
    """Questions (with options and the recommended pick) to ask the owner: which courses, which folder,
    what to do with courses that have no subject folder. Then call moodle_setup_apply."""
    return _safe(core.setup_questions)


@mcp.tool()
def moodle_setup_apply(course_ids: list[int] | None = None, master_dir: str | None = None,
                       unmatched: str | None = None) -> str:
    """Save the setup answers. course_ids: courses to download. master_dir: the Master folder.
    unmatched: 'random' (Master/Random/Moodle/<course>) or 'skip'."""
    return _safe(core.setup_apply, course_ids, master_dir, unmatched)


@mcp.tool()
def moodle_open_setup_page() -> str:
    """Open the local setup page in the browser (login button, course checkboxes, folder)."""
    return _safe(lambda: {"opened": setup_page.start(open_browser=True)})


@mcp.tool()
def list_courses() -> str:
    """The owner's enrolled Moodle courses with id, subject and the folder their files go to."""
    return _safe(core.list_courses)


@mcp.tool()
def get_course_contents(course: str) -> str:
    """Sections, activities and files of one course. course: id, or part of the name or subject."""
    return _safe(core.get_course_contents, course)


@mcp.tool()
def download(course: str = "all", only_new: bool = True) -> str:
    """Download course files into Master/<subject folder>/Moodle/. course: id, name part, or 'all'
    (the courses chosen in setup). only_new=True skips files already downloaded and unchanged."""
    return _safe(core.download, course, only_new)


@mcp.tool()
def list_assignments(course: str = "all", due_after: str = "today") -> str:
    """Assignments with due dates. course: id, name part or 'all'. due_after: 'today', 'all' or YYYY-MM-DD."""
    return _safe(core.list_assignments, course, due_after)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
