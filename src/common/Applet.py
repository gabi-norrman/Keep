"""An AppleScript applet that runs one shell script of the tree, built with osacompile and given
the Keep icon: the Desktop shortcut (`common/DesktopShortcut`) and the login item
(`common/Autostart`) are two of them.

An applet is a real Mach-O program, so macOS holds it, not `/bin/sh`, responsible for what its
script does and asks the user for the grants it needs (a repository under ~/Documents) with the
applet's own name on the dialog. A plain script bundle gets no dialog and no access.

The applet stays out of the Dock and Cmd-Tab (`LSUIElement`: the watchdog launches it every few
minutes) and shows no dialog when its script fails — a failure is appended to `FAILURE_LOG`
instead (outside the checkout, so writing it needs no grant), since nobody is at the Mac to
dismiss one. A bundle built before either of those is a `GENERATION` behind and rebuilt; macOS
then asks for the applet's grants once more.
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


# The Info.plist key that marks the current generation: it hides the applet from the Dock, and
# a bundle without it was also built without the error handler of `source`.
GENERATION = "LSUIElement"
FAILURE_LOG = Path.home() / "Library" / "Logs" / "Keep" / "applet.log"


def source(script: Path) -> str:
    """`do shell script` on the script's path (AppleScript's `quoted form` does the shell quoting),
    with the failure logged instead of shown: an applet left to its own error handling pops a
    dialog that waits for someone to dismiss it."""
    log = literal(FAILURE_LOG)
    return (f"try\ndo shell script quoted form of {literal(script)}\n"
            f"on error message\n"
            f"do shell script \"mkdir -p \" & quoted form of {literal(FAILURE_LOG.parent)} & \" && printf '%s %s\\n' "
            f"\\\"$(date '+%F %T')\\\" \" & quoted form of message & \" >> \" & quoted form of {log}\n"
            f"end try")


def current(app: Path) -> bool:
    """Whether the bundle is there and of the current generation."""
    plist = app / "Contents" / "Info.plist"
    return plist.exists() and GENERATION in plist.read_text(errors="replace")


def build(app: Path, script: Path, icon: Path = ICON, run=subprocess.run) -> bool:
    """Build the applet at `app` running `script` unless a current one is there. Returns whether
    it is there now; a failed build is logged with osacompile's words."""
    if current(app):
        trace(f"Applet: {app} is already there")
        return True
    if app.exists():
        info(f"Applet: {app.name} is of an earlier generation — rebuilding it (its grants are asked for again)")
        shutil.rmtree(app, ignore_errors=True)
    try:
        result = run(["osacompile", "-o", str(app), "-e", source(script)], capture_output=True, text=True)
    except OSError as e:
        error(f"Applet: osacompile could not run ({e}) — {app.name} not built")
        return False
    if result.returncode != 0:
        error(f"Applet: osacompile failed ({result.returncode}) for {app.name}: {(result.stderr or result.stdout).strip()}")
        return False
    _finish(app, icon, run)
    info(f"Applet: built {app} running {script}")
    return True


def _finish(app: Path, icon: Path, run):
    """Give the applet the Keep icon and keep it out of the Dock. Its Info.plist names `applet`
    as the icon file, but Finder prefers the asset catalog osacompile ships (`Assets.car`, named
    by `CFBundleIconName`), so both go; `GENERATION` is set; the bundle is then re-signed ad hoc,
    as osacompile signed it, and touched so Finder re-reads it."""
    contents = app / "Contents"
    plist = contents / "Info.plist"
    steps = []
    if icon.exists():
        shutil.copyfile(icon, contents / "Resources" / "applet.icns")
        (contents / "Resources" / "Assets.car").unlink(missing_ok=True)
        steps.append(["plutil", "-remove", "CFBundleIconName", str(plist)])
    else:
        info(f"Applet: icon {icon} missing — {app.name} keeps the stock icon")
    steps += [["plutil", "-replace", GENERATION, "-bool", "true", str(plist)],
              ["codesign", "--force", "-s", "-", str(app)]]
    for argv in steps:
        result = run(argv, capture_output=True, text=True)
        if result.returncode != 0:
            info(f"Applet: {argv[0]} failed ({result.returncode}) for {app.name}: {(result.stderr or result.stdout).strip()}")
    app.touch()
