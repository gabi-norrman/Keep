"""The applet builder: osacompile on a `do shell script` source, the Keep icon in place of the
stock one, an existing bundle left alone."""
from pathlib import Path
from types import SimpleNamespace

from common import Applet


def osacompile(calls, returncode=0, make_bundle=True):
    """A stand-in for subprocess.run that records every argv and, for osacompile, lays out the
    applet the way it does: Contents/Info.plist and Resources/Assets.car inside the -o bundle.
    plutil and codesign are recorded and succeed."""
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] != "osacompile":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if make_bundle:
            contents = Path(argv[argv.index("-o") + 1]) / "Contents"
            (contents / "Resources").mkdir(parents=True)
            (contents / "Resources" / "Assets.car").write_bytes(b"car")
            (contents / "Info.plist").write_text("<plist/>")
        return SimpleNamespace(returncode=returncode, stdout="", stderr="syntax error" if returncode else "")
    return run


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
        assert text.startswith("do shell script quoted form of ")
        assert '\\"Keep\\"' in text

    def test_an_existing_applet_is_left_alone(self, tmp_path):
        app = tmp_path / "Thing.app"
        app.mkdir()
        calls = []
        assert Applet.build(app, tmp_path / "thing.sh", tmp_path / "none.icns", run=osacompile(calls)) is True
        assert calls == []

    def test_a_failed_compile_is_reported_as_not_built(self, tmp_path):
        assert Applet.build(tmp_path / "Thing.app", tmp_path / "thing.sh", tmp_path / "none.icns",
                            run=osacompile([], returncode=1, make_bundle=False)) is False

    def test_a_missing_icon_still_builds_the_applet(self, tmp_path):
        app = tmp_path / "Thing.app"
        assert Applet.build(app, tmp_path / "thing.sh", tmp_path / "none.icns", run=osacompile([])) is True
        assert not (app / "Contents" / "Resources" / "applet.icns").exists()

    def test_the_shipped_icon_is_an_icns_file(self):
        assert Applet.ICON.read_bytes()[:4] == b"icns"
