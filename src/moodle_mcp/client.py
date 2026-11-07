"""Small Moodle web-service client (REST, JSON)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class MoodleError(Exception):
    """An error with a plain-words message and the next step."""

    def __init__(self, message: str, next_step: str = "", code: str = ""):
        super().__init__(message)
        self.message = message
        self.next_step = next_step
        self.code = code

    def __str__(self) -> str:
        return f"{self.message} Next step: {self.next_step}" if self.next_step else self.message


def _flatten(prefix: str, value: Any, out: list[tuple[str, str]]) -> None:
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    elif isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]", v, out)
    elif isinstance(value, bool):
        out.append((prefix, "1" if value else "0"))
    elif value is not None:
        out.append((prefix, str(value)))


def plain_error(code: str, raw: str) -> MoodleError:
    if code in {"invalidtoken", "invalidtokensession"}:
        return MoodleError("Moodle says the login token is not valid (it expired or was removed).",
                           "run moodle_login and log in again in the browser.", code)
    if code in {"accessexception", "servicenotavailable", "webservicesnotenabled"}:
        return MoodleError(f"Moodle does not allow this action for the app token ({raw}).",
                           "nothing to fix here; this part of Moodle is closed to the app.", code)
    if code in {"requireloginerror", "errorcoursecontextnotvalid", "notenrolled"}:
        return MoodleError(f"You have no access to this course ({raw}).",
                           "check the course id with list_courses.", code)
    return MoodleError(f"Moodle returned an error: {raw}", "try again; if it repeats, run moodle_status.", code)


class MoodleClient:
    def __init__(self, base_url: str, token: str, timeout: float = 60):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._site: dict | None = None

    # --- web service -----------------------------------------------------
    def call(self, function: str, **params: Any) -> Any:
        fields: list[tuple[str, str]] = [("wstoken", self.token), ("wsfunction", function),
                                         ("moodlewsrestformat", "json")]
        for k, v in params.items():
            _flatten(k, v, fields)
        data = urllib.parse.urlencode(fields).encode()
        url = f"{self.base_url}/webservice/rest/server.php"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=self.timeout) as r:
                body = r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise MoodleError(f"Moodle answered HTTP {e.code}.", "try again later; Moodle may be down.") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise MoodleError(f"Cannot reach Moodle ({getattr(e, 'reason', e)}).",
                              "check the internet connection, then try again.") from None
        try:
            result = json.loads(body)
        except json.JSONDecodeError:
            raise MoodleError("Moodle sent a page that is not data (maybe maintenance).",
                              "try again in a few minutes.") from None
        if isinstance(result, dict) and "exception" in result:
            raise plain_error(result.get("errorcode", ""), result.get("message", ""))
        return result

    # --- helpers ---------------------------------------------------------
    def site_info(self) -> dict:
        if self._site is None:
            self._site = self.call("core_webservice_get_site_info")
        return self._site

    def courses(self) -> list[dict]:
        uid = self.site_info()["userid"]
        return self.call("core_enrol_get_users_courses", userid=uid)

    def contents(self, course_id: int) -> list[dict]:
        return self.call("core_course_get_contents", courseid=course_id)

    def assignments(self, course_ids: list[int]) -> list[dict]:
        res = self.call("mod_assign_get_assignments", courseids=course_ids)
        return res.get("courses", [])

    def file_url(self, fileurl: str) -> str:
        """Add the token to a pluginfile URL so it downloads without a browser session."""
        if "/pluginfile.php" in fileurl and "/webservice/pluginfile.php" not in fileurl:
            fileurl = fileurl.replace("/pluginfile.php", "/webservice/pluginfile.php", 1)
        sep = "&" if "?" in fileurl else "?"
        return f"{fileurl}{sep}token={urllib.parse.quote(self.token)}"

    def download(self, fileurl: str, dest_tmp: str) -> int:
        """Stream a file to dest_tmp. Returns bytes written."""
        url = self.file_url(fileurl)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r, open(dest_tmp, "wb") as f:
                ctype = r.headers.get("Content-Type", "")
                first = r.read(65536)
                if ctype.startswith("application/json") and first.lstrip().startswith(b"{"):
                    try:
                        err = json.loads(first)
                        if "errorcode" in err or "error" in err:
                            raise plain_error(err.get("errorcode", ""), err.get("error") or err.get("message", ""))
                    except json.JSONDecodeError:
                        pass
                n = 0
                while first:
                    f.write(first)
                    n += len(first)
                    first = r.read(1 << 20)
                return n
        except urllib.error.HTTPError as e:
            raise MoodleError(f"Download failed with HTTP {e.code}.", "run download again later.") from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise MoodleError(f"Download failed ({getattr(e, 'reason', e)}).",
                              "check the internet connection, then run download again.") from None
