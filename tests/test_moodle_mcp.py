import asyncio
import base64
import hashlib
import json
import os
import urllib.request
from pathlib import Path

import pytest

from conftest import has_chrome
from fake_moodle import TOKEN
from moodle_mcp import core, login, setup_page
from moodle_mcp.client import MoodleError
from moodle_mcp.config import safe_name


def logged_in(env):
    env["store"][env["fake"].url] = TOKEN


# --- status, errors --------------------------------------------------------
def test_status_not_logged_in(env):
    st = core.status()
    assert st["logged_in"] is False
    assert "moodle_login" in st["next_step"]


def test_status_expired_token_plain_words(env):
    env["store"][env["fake"].url] = "old"
    st = core.status()
    assert st["token_valid"] is False
    assert "moodle_login" in st["next_step"]


def test_tools_need_login(env):
    with pytest.raises(MoodleError) as e:
        core.list_courses()
    assert "moodle_login" in e.value.next_step


def test_status_ready_after_setup(env):
    logged_in(env)
    assert "setup" in core.status()["next_step"]
    core.setup_apply([2702, 736], None, "random")
    st = core.status()
    assert st["token_valid"] and st["user"] == "Test Student"
    assert st["next_step"].startswith("ready")


# --- setup ------------------------------------------------------------------
def test_setup_questions_recommend_yaml_courses(env):
    logged_in(env)
    q = {x["key"]: x for x in core.setup_questions()["questions"]}
    assert sorted(q["course_ids"]["recommended"]) == [736, 2702]
    assert {o["value"] for o in q["course_ids"]["options"]} == {736, 2702, 3692}


def test_setup_questions_without_login_use_yaml(env):
    q = {x["key"]: x for x in core.setup_questions()["questions"]}
    assert {o["value"] for o in q["course_ids"]["options"]} == {2702, 736, 999, 3692}


def test_setup_apply_rejects_missing_folder(env):
    with pytest.raises(MoodleError):
        core.setup_apply(master_dir=str(env["tmp"] / "nope"))


# --- courses and contents ---------------------------------------------------
def test_list_courses_folders(env):
    logged_in(env)
    rows = {r["id"]: r for r in core.list_courses()}
    m = env["master"]
    assert rows[2702]["folder"] == str(m / "53001202-M4 Sistemas Integrados de Fabricación" / "Moodle")
    assert rows[3692]["folder"] == str(m / "Random" / "Moodle" / "DSE")
    assert rows[2702]["subject"] == "Sistemas Integrados de Fabricación"


def test_contents_by_name(env):
    logged_in(env)
    c = core.get_course_contents("fabricacion")  # no accent, lower case
    assert c["course_id"] == 2702
    names = [m["name"] for m in c["sections"][0]["modules"]]
    assert "Tema 1 slides" in names


def test_ambiguous_course_name(env):
    logged_in(env)
    with pytest.raises(MoodleError) as e:
        core.get_course_contents("d")
    assert "more than one" in e.value.message


# --- download -----------------------------------------------------------------
def test_download_then_only_new(env):
    logged_in(env)
    fake = env["fake"]
    r = core.download("2702")
    base = env["master"] / "53001202-M4 Sistemas Integrados de Fabricación" / "Moodle"
    assert (base / "00 Tema 1_ Sistemas" / "Tema1.pdf").read_bytes() == b"%PDF tema 1"
    assert (base / "00 Tema 1_ Sistemas" / "Material_ extra" / "sub" / "Datos.xlsx").exists()
    assert (base / "Tareas" / "Práctica 1" / "Enunciado.pdf").exists()
    assert r["downloaded"] == 4
    assert os.path.getmtime(base / "00 Tema 1_ Sistemas" / "Tema1.pdf") == fake.timemodified
    # No token in the download index.
    idx = (env["tmp"] / "home" / "downloads.json").read_text(encoding="utf-8")
    assert TOKEN not in idx

    fake.downloads.clear()
    r2 = core.download("2702")
    assert r2["downloaded"] == 0 and fake.downloads == []

    fake.extra_file = True
    r3 = core.download("2702")
    assert r3["courses"][0]["new"] == [str(Path("00 Tema 1_ Sistemas") / "Tema2.pdf")]

    fake.timemodified += 10
    r4 = core.download("2702")
    assert len(r4["courses"][0]["updated"]) == 5


def test_download_all_needs_setup(env):
    logged_in(env)
    with pytest.raises(MoodleError) as e:
        core.download("all")
    assert "setup" in e.value.next_step


def test_download_all_and_skip_unmatched(env):
    logged_in(env)
    core.setup_apply([2702, 736, 3692], None, "skip")
    r = core.download("all")
    by = {c["course_id"]: c for c in r["courses"]}
    assert "skipped" in by[3692]
    assert (env["master"] / "53001212-M4 Dirección Financiera" / "Moodle" / "00 General" / "DF1-Contabilidad.pdf").exists()


def test_download_expired_token(env):
    env["store"][env["fake"].url] = "old"
    with pytest.raises(MoodleError) as e:
        core.download("2702")
    assert "moodle_login" in e.value.next_step


# --- assignments ----------------------------------------------------------------
def test_assignments_due_dates(env):
    logged_in(env)
    a = core.list_assignments("2702", "today")
    assert [x["name"] for x in a["assignments"]] == ["Práctica 1"]
    assert a["assignments"][0]["due"].startswith("2100-01-01") or a["assignments"][0]["due"].startswith("2099-12-31")
    allx = core.list_assignments("2702", "all")
    assert allx["count"] == 2


# --- helpers ----------------------------------------------------------------------
def test_safe_name():
    assert safe_name('a<b>:c"d/e\\f|g?h*') == "a_b__c_d_e_f_g_h_"
    assert safe_name("CON.txt") == "_CON.txt"
    assert safe_name("x" * 200 + ".pdf").endswith(".pdf") and len(safe_name("x" * 200 + ".pdf")) == 80
    assert safe_name("name. ") == "name"


def test_parse_token_signature():
    base, passport = "https://m.example/x", "abc"
    sig = hashlib.md5((base + passport).encode()).hexdigest()
    raw = base64.b64encode(f"{sig}:::TOK:::priv".encode()).decode()
    assert login.parse_token(f"moodlemobile://token={raw}", [base], passport) == "TOK"
    with pytest.raises(MoodleError):
        login.parse_token(raw, [base], "other")


# --- MCP layer --------------------------------------------------------------------
def test_mcp_tools_listed_and_errors_are_plain(env):
    from moodle_mcp.server import mcp

    async def run():
        names = {t.name for t in await mcp.list_tools()}
        res = await mcp.call_tool("list_courses", {})
        return names, res

    names, res = asyncio.run(run())
    assert {"moodle_status", "moodle_login", "moodle_setup_questions", "moodle_setup_apply",
            "moodle_open_setup_page", "list_courses", "get_course_contents", "download",
            "list_assignments"} <= names
    assert "moodle_login" in json.dumps(res, default=str)


# --- setup page -------------------------------------------------------------------
def test_setup_page_save(env):
    logged_in(env)
    url = setup_page.start(open_browser=False)
    try:
        html = urllib.request.urlopen(url).read().decode()
        assert "Courses to download" in html
        state = json.loads(urllib.request.urlopen(url + "api/state").read())
        assert state["status"]["token_valid"]
        req = urllib.request.Request(url + "api/save", data=json.dumps({"course_ids": [736]}).encode(),
                                     headers={"X-Moodle-MCP": "1", "Content-Type": "application/json"})
        assert json.loads(urllib.request.urlopen(req).read())["course_ids"] == [736]
        # Without the header (another web site) the page refuses.
        bad = urllib.request.Request(url + "api/save", data=b"{}")
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(bad)
        # Wrong path key: 404.
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(url.rsplit("/", 2)[0] + "/wrong/")
    finally:
        setup_page.stop()


# --- browser token catch (real Chrome, fake Moodle) -----------------------------
@pytest.mark.skipif(not has_chrome(), reason="no Chrome")
def test_browser_catch_token(env):
    fake = env["fake"]
    tok = login.catch_token(fake.url, [fake.url], env["tmp"] / "profile", timeout=60)
    assert tok == TOKEN


@pytest.mark.skipif(not has_chrome(), reason="no Chrome")
def test_browser_catch_token_after_sso_lands_on_dashboard(env):
    fake = env["fake"]
    fake.lose_return = True
    tok = login.catch_token(fake.url, [fake.url], env["tmp"] / "profile2", timeout=60)
    assert tok == TOKEN and fake.launch_visits == 2


def test_is_after_login():
    b = "https://m.x/t"
    assert login.is_after_login(b + "/my/", b)
    assert not login.is_after_login(b + "/login/index.php", b)
    assert not login.is_after_login(b + "/auth/oauth2/login.php", b)
    assert not login.is_after_login(b + "/admin/tool/mobile/launch.php?x", b)
    assert not login.is_after_login("https://sso.upm.es/x", b)
