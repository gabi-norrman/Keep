"""The Keep.app shortcut on the Desktop, an AppleScript applet that runs `keep.sh`, built once
the first-time setup sees Full Disk Access granted (`common/FullDiskAccess`).

`keep.sh` opens a terminal (Ghostty, else iTerm2, else Terminal) and attaches the console to
the running server, or starts the server first; the applet only runs that script, so the server
is still launched by start.sh through Terminal and the Mac's grants keep applying to it.
"""
import shutil
import subprocess
from pathlib import Path

from common.Paths import AISERVER_DIR, RES_DIR
from common.logging import info, trace, error

DESKTOP = Path.home() / "Desktop"
APP_NAME = "Keep.app"
ICON = RES_DIR / "keep.icns"
SCRIPT_NAME = "keep.sh"


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
    if icon.exists():
        _brand(app, icon, run)
    else:
        info(f"Desktop shortcut: icon {icon} missing — the applet keeps the stock icon")
    info(f"Desktop shortcut: built {app} running {script}")
    return True


def _brand(app: Path, icon: Path, run):
    """Give the applet the Keep icon. Its Info.plist names `applet` as the icon file, but Finder
    prefers the asset catalog osacompile ships (`Assets.car`, named by `CFBundleIconName`), so
    both go; the bundle is then re-signed ad hoc, as osacompile signed it, and touched so Finder
    re-reads it."""
    contents = app / "Contents"
    shutil.copyfile(icon, contents / "Resources" / "applet.icns")
    (contents / "Resources" / "Assets.car").unlink(missing_ok=True)
    for argv in (["plutil", "-remove", "CFBundleIconName", str(contents / "Info.plist")],
                 ["codesign", "--force", "-s", "-", str(app)]):
        result = run(argv, capture_output=True, text=True)
        if result.returncode != 0:
            info(f"Desktop shortcut: {argv[0]} failed ({result.returncode}): {(result.stderr or result.stdout).strip()}")
    app.touch()
