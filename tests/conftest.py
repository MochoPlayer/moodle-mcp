import shutil
from pathlib import Path

import pytest

from fake_moodle import FakeMoodle

YAML = """moodle_base_url: x
subjects:
  - code: A
    name: Sistemas Integrados de Fabricación
    folder: "53001202-M4 Sistemas Integrados de Fabricación"
    moodle:
      course_id: 2702
      course_name: "05AZ. MII - Sistemas Integrados de Fabricación"
  - code: B
    name: Dirección Financiera
    folder: "53001212-M4 Dirección Financiera"
    moodle:
      course_id: 736
      course_name: "Dirección financiera"
      related_course_id: 999
      related_course_name: "DF extra"
unmatched_moodle_courses:
  - course_id: 3692
    course_name: DSE
"""


@pytest.fixture
def fake():
    f = FakeMoodle()
    yield f
    f.close()


@pytest.fixture
def env(tmp_path, monkeypatch, fake):
    """Isolated settings folder, Master folder and an in-memory token store."""
    master = tmp_path / "Master"
    master.mkdir()
    (master / "moodle-watch.yaml").write_text(YAML, encoding="utf-8")
    monkeypatch.setenv("MOODLE_MCP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MOODLE_MCP_BASE_URL", fake.url)
    monkeypatch.setenv("MOODLE_MCP_MASTER_DIR", str(master))
    store: dict[str, str] = {}
    from moodle_mcp import tokenstore
    monkeypatch.setattr(tokenstore, "get_token", lambda b: store.get(b))
    monkeypatch.setattr(tokenstore, "set_token", lambda b, t: store.__setitem__(b, t))
    monkeypatch.setattr(tokenstore, "delete_token", lambda b: store.pop(b, None))
    return {"master": master, "store": store, "fake": fake, "tmp": tmp_path}


def has_chrome() -> bool:
    from moodle_mcp.login import find_browser
    try:
        find_browser()
        return True
    except Exception:
        return False
