# moodle-mcp

MCP server for UPM Moodle (`https://moodle.upm.es/titulaciones/oficiales`). Claude uses it to log in,
list courses and their contents, download files (only new ones on the next run) and list assignments
with due dates. The owner never types a command: Claude starts the login and asks the setup questions.

## Tools

| Tool | What it does |
|---|---|
| `moodle_status` | Logged in? Token valid? Setup done? Gives the next step in plain words. |
| `moodle_login` | Opens the UPM login in a Chrome window and catches the token by itself. |
| `moodle_logout` | Removes the token from Windows Credential Manager. |
| `moodle_setup_questions` / `moodle_setup_apply` | Questions with options for the agent to ask, then saves the answers. |
| `moodle_open_setup_page` | Opens a local page: login button, course checkboxes, Master folder. |
| `list_courses` | Enrolled courses, their subject and target folder. |
| `get_course_contents(course)` | Sections, activities, files and links of one course. |
| `download(course="all", only_new=True)` | Files to `Master\<subject folder>\Moodle\`. |
| `list_assignments(course="all", due_after="today")` | Assignments with due dates. |

`course` is a course id or part of the course or subject name (accents and case do not matter).

## How the login works

No registry change and no copy and paste:

1. Chrome starts with its own profile (`%LOCALAPPDATA%\moodle-mcp\login-browser`) and a DevTools port on 127.0.0.1.
2. It opens Moodle's mobile-app launch page (`admin/tool/mobile/launch.php?service=moodle_mobile_app`).
3. After the UPM login, Moodle answers with a redirect to `moodlemobile://token=...`. The DevTools Fetch
   domain pauses that response, the server reads the token, checks its signature (md5 of site URL + passport)
   and shows a "Login done" page. The window closes by itself.
4. The token goes to Windows Credential Manager (`keyring`, service `moodle-mcp`). It is never written to a file or log.

## Files

- Course map: `Master\moodle-watch.yaml` (`course_id` -> subject `folder`). A related course shell
  (INGENIA) goes to `<folder>\Moodle\<course name>\`. Courses not in the yaml go to
  `Master\Random\Moodle\<course>` or are skipped (setup choice).
- Settings and the download index (no secrets): `%LOCALAPPDATA%\moodle-mcp\settings.json`, `downloads.json`.
- A file is downloaded again only when Moodle reports a new `timemodified` or size, or the local copy is gone.

## Run, test, register

```
uv sync
uv run pytest            # fake Moodle; one test drives real Chrome against a fake launch page
claude mcp add moodle --scope user -- uv run --quiet --directory C:\Users\mcast\moodle-mcp moodle-mcp
```
