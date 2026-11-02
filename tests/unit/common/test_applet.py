"""The applet builder: osacompile on a `do shell script` source that logs a failure instead of
showing it, the Keep icon in place of the stock one, the bundle kept out of the Dock, a current
bundle left alone and an earlier generation rebuilt."""
from pathlib import Path
from types import SimpleNamespace

from common import Applet


def osacompile(calls, returncode=0, make_bundle=True):
    """A stand-in for subprocess.run that records every argv and, for osacompile, lays out the
    applet the way it does: Contents/Info.plist and Resources/Assets.car inside the -o bundle.
    plutil writes the key it is given (as the real one does); codesign is recorded and succeeds."""
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "plutil" and argv[1] == "-replace":
            plist = Path(argv[-1])
            plist.write_text(plist.read_text().replace("</plist>", f"<key>{argv[2]}</key><true/></plist>"))
        if argv[0] != "osacompile":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if make_bundle:
            contents = Path(argv[argv.index("-o") + 1]) / "Contents"
            (contents / "Resources").mkdir(parents=True)
            (contents / "Resources" / "Assets.car").write_bytes(b"car")
            (contents / "Info.plist").write_text("<plist></plist>")
        return SimpleNamespace(returncode=returncode, stdout="", stderr="syntax error" if returncode else "")
    return run


def current_bundle(app: Path):
    """A bundle of the current generation, as a finished build leaves it."""
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_text(f"<plist><key>{Applet.GENERATION}</key><true/></plist>")


class TestBuild:
    def test_compiles_a_do_shell_script_applet_with_the_icon(self, tmp_path):
        app, script, icon = tmp_path / "Thing.app", tmp_path / "Keep" / "thing.sh", tmp_path / "keep.icns"
        icon.write_bytes(b"icns")
        calls = []
        assert Applet.build(app, script, icon, run=osacompile(calls)) is True
        argv = calls[0]
        assert argv[0] == "osacompile"
        assert argv[argv.index("-o") + 1] == str(app)
        assert str(script) in argv[argv.index("-e") + 1]
        assert (app / "Contents" / "Resources" / "applet.icns").read_bytes() == b"icns"

    def test_the_stock_icon_catalog_is_removed_and_the_bundle_resigned(self, tmp_path):
        """Finder shows the asset catalog's icon over applet.icns while the catalog is there."""
        app, icon = tmp_path / "Thing.app", tmp_path / "keep.icns"
        icon.write_bytes(b"icns")
        calls = []
        Applet.build(app, tmp_path / "thing.sh", icon, run=osacompile(calls))
        assert not (app / "Contents" / "Resources" / "Assets.car").exists()
        assert ["plutil", "-remove", "CFBundleIconName", str(app / "Contents" / "Info.plist")] in calls
        assert ["codesign", "--force", "-s", "-", str(app)] in calls

    def test_a_path_with_quotes_is_escaped_in_the_applescript_literal(self, tmp_path):
        text = Applet.source(tmp_path / 'My "Keep"' / "keep.sh")
        assert "do shell script quoted form of " in text
        assert '\\"Keep\\"' in text

    def test_the_source_logs_a_failed_script_instead_of_showing_a_dialog(self, tmp_path):
        """An applet whose script fails pops an error dialog unless the script runs inside a
        try block; the watchdog runs it with nobody at the Mac to dismiss one."""
        lines = Applet.source(tmp_path / "keep.sh").splitlines()
        assert lines[0] == "try" and lines[-1] == "end try"
        assert any(str(Applet.FAILURE_LOG) in line for line in lines[lines.index("on error message"):])

    def test_the_bundle_is_kept_out_of_the_dock(self, tmp_path):
        """Launched every few minutes by the watchdog, an applet in the Dock would flicker."""
        app = tmp_path / "Thing.app"
        calls = []
        Applet.build(app, tmp_path / "thing.sh", tmp_path / "none.icns", run=osacompile(calls))
        plutil = [c for c in calls if c[0] == "plutil" and c[1] == "-replace"]
        assert plutil and plutil[0][2] == "LSUIElement" and plutil[0][-1] == str(app / "Contents" / "Info.plist")
        assert Applet.current(app)
        assert calls.index(plutil[0]) < calls.index(["codesign", "--force", "-s", "-", str(app)])

    def test_a_current_applet_is_left_alone(self, tmp_path):
        app = tmp_path / "Thing.app"
        current_bundle(app)
        calls = []
        assert Applet.build(app, tmp_path / "thing.sh", tmp_path / "none.icns", run=osacompile(calls)) is True
        assert calls == []

    def test_an_applet_of_an_earlier_generation_is_rebuilt(self, tmp_path):
        """A bundle from before the Dock and dialog changes would flicker and could block."""
        app = tmp_path / "Thing.app"
        (app / "Contents" / "Resources").mkdir(parents=True)
        (app / "Contents" / "Info.plist").write_text("<plist></plist>")
        (app / "Contents" / "Resources" / "old.txt").write_text("stale")
        calls = []
        assert Applet.build(app, tmp_path / "thing.sh", tmp_path / "none.icns", run=osacompile(calls)) is True
        assert calls[0][0] == "osacompile"
        assert not (app / "Contents" / "Resources" / "old.txt").exists()
        assert Applet.current(app)

    def test_a_failed_compile_is_reported_as_not_built(self, tmp_path):
        assert Applet.build(tmp_path / "Thing.app", tmp_path / "thing.sh", tmp_path / "none.icns",
                            run=osacompile([], returncode=1, make_bundle=False)) is False

    def test_a_missing_icon_still_builds_the_applet(self, tmp_path):
        app = tmp_path / "Thing.app"
        assert Applet.build(app, tmp_path / "thing.sh", tmp_path / "none.icns", run=osacompile([])) is True
        assert not (app / "Contents" / "Resources" / "applet.icns").exists()

    def test_the_shipped_icon_is_an_icns_file(self):
        assert Applet.ICON.read_bytes()[:4] == b"icns"
