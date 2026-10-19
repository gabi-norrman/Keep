"""The Keep.app shortcut on the Desktop, an AppleScript applet that runs `keep.sh`, built once
the first-time setup sees Full Disk Access granted.

`keep.sh` opens a terminal (Ghostty, else iTerm2, else Terminal) and attaches the console to
the running server, or starts the server first; the applet only runs that script, so the server
is still launched by start.sh through Terminal and the Mac's grants keep applying to it. The
probe for the grant is whether this process may read chat.db, the file macOS guards with Full
Disk Access — the same test the iMessage integration's health check makes.
"""
import shutil
import sqlite3
import subprocess
from pathlib import Path

from common.Paths import AISERVER_DIR, RES_DIR
from common.logging import info, trace, error

CHAT_DB = Path.home() / "Library/Messages/chat.db"
DESKTOP = Path.home() / "Desktop"
APP_NAME = "Keep.app"
ICON = RES_DIR / "keep.icns"
SCRIPT_NAME = "keep.sh"


def full_disk_access_granted(chat_db: Path = CHAT_DB) -> bool:
    """Whether this process may read chat.db. A Mac without Messages set up has no file and
    answers False: the grant is then not observable this way."""
    if not chat_db.exists():
        trace(f"Full Disk Access probe: {chat_db} does not exist — nothing to probe")
        return False
    try:
        with sqlite3.connect(f"file:{chat_db}?mode=ro", uri=True) as conn:
            conn.execute("SELECT 1 FROM message LIMIT 1")
    except sqlite3.Error as e:   # OperationalError when macOS refuses the file; any failure means no access
        trace(f"Full Disk Access probe: {chat_db.name} cannot be read ({e}) — not granted")
        return False
    return True


def _applescript(script: Path) -> str:
    """`do shell script` on the script's path; AppleScript's `quoted form` does the shell quoting,
    the string literal needs only its own backslashes and quotes escaped."""
    literal = str(script).replace("\\", "\\\\").replace('"', '\\"')
    return f'do shell script quoted form of "{literal}"'


def ensure(desktop: Path = DESKTOP, repo: Path = AISERVER_DIR, icon: Path = ICON, run=subprocess.run) -> bool:
    """Build `Keep.app` on the Desktop unless it is there. Returns whether it is there now; a
    failed build is logged with osacompile's words."""
    app = desktop / APP_NAME
    if app.exists():
        trace(f"Desktop shortcut: {app} is already there")
        return True
    if not desktop.is_dir():
        info(f"Desktop shortcut: no {desktop} folder — not built")
        return False
    script = repo / SCRIPT_NAME
    try:
        result = run(["osacompile", "-o", str(app), "-e", _applescript(script)], capture_output=True, text=True)
    except OSError as e:
        error(f"Desktop shortcut: osacompile could not run ({e}) — not built")
        return False
    if result.returncode != 0:
        error(f"Desktop shortcut: osacompile failed ({result.returncode}): {(result.stderr or result.stdout).strip()}")
        return False
    resources = app / "Contents" / "Resources"
    if icon.exists() and resources.is_dir():
        shutil.copyfile(icon, resources / "applet.icns")   # the applet's Info.plist names `applet` as its icon
        app.touch()                                         # Finder re-reads the icon on a changed bundle
    else:
        info(f"Desktop shortcut: icon {icon} or {resources} missing — the applet keeps the stock icon")
    info(f"Desktop shortcut: built {app} running {script}")
    return True
