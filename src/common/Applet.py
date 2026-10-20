"""An AppleScript applet that runs one shell script of the tree, built with osacompile and given
the Keep icon: the Desktop shortcut (`common/DesktopShortcut`) and the login item
(`common/Autostart`) are two of them.

An applet is a real Mach-O program, so macOS holds it, not `/bin/sh`, responsible for what its
script does and asks the user for the grants it needs (a repository under ~/Documents) with the
applet's own name on the dialog. A plain script bundle gets no dialog and no access.
"""
import shutil
import subprocess
from pathlib import Path

from common.Paths import RES_DIR
from common.logging import info, trace, error

ICON = RES_DIR / "keep.icns"


def literal(path: Path) -> str:
    """`path` as an AppleScript string literal: only its backslashes and quotes need escaping."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def source(script: Path) -> str:
    """`do shell script` on the script's path; AppleScript's `quoted form` does the shell quoting."""
    return f"do shell script quoted form of {literal(script)}"


def build(app: Path, script: Path, icon: Path = ICON, run=subprocess.run) -> bool:
    """Build the applet at `app` running `script` unless it is there. Returns whether it is there
    now; a failed build is logged with osacompile's words."""
    if app.exists():
        trace(f"Applet: {app} is already there")
        return True
    try:
        result = run(["osacompile", "-o", str(app), "-e", source(script)], capture_output=True, text=True)
    except OSError as e:
        error(f"Applet: osacompile could not run ({e}) — {app.name} not built")
        return False
    if result.returncode != 0:
        error(f"Applet: osacompile failed ({result.returncode}) for {app.name}: {(result.stderr or result.stdout).strip()}")
        return False
    if icon.exists():
        _brand(app, icon, run)
    else:
        info(f"Applet: icon {icon} missing — {app.name} keeps the stock icon")
    info(f"Applet: built {app} running {script}")
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
            info(f"Applet: {argv[0]} failed ({result.returncode}) for {app.name}: {(result.stderr or result.stdout).strip()}")
    app.touch()
