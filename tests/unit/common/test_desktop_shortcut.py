"""The Desktop shortcut: the Full Disk Access probe on chat.db and the applet built from
keep.sh with the Keep icon."""
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from common import DesktopShortcut


class TestFullDiskAccessProbe:
    def test_a_readable_messages_database_means_granted(self, tmp_path):
        db = tmp_path / "chat.db"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE message (ROWID INTEGER PRIMARY KEY)")
        assert DesktopShortcut.full_disk_access_granted(db) is True

    def test_a_database_that_cannot_be_opened_means_not_granted(self, tmp_path):
        db = tmp_path / "chat.db"
        db.write_text("not a database")
        assert DesktopShortcut.full_disk_access_granted(db) is False

    def test_no_messages_database_means_not_granted(self, tmp_path):
        assert DesktopShortcut.full_disk_access_granted(tmp_path / "chat.db") is False


def _osacompile(calls, returncode=0, make_bundle=True):
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


class TestEnsure:
    def test_builds_the_applet_running_keep_sh_with_the_icon(self, tmp_path):
        desktop, repo, icon = tmp_path / "Desktop", tmp_path / "Keep", tmp_path / "keep.icns"
        desktop.mkdir()
        icon.write_bytes(b"icns")
        calls = []
        assert DesktopShortcut.ensure(desktop, repo, icon, run=_osacompile(calls)) is True
        argv = calls[0]
        assert argv[0] == "osacompile"
        assert argv[argv.index("-o") + 1] == str(desktop / "Keep.app")
        assert str(repo / "keep.sh") in argv[argv.index("-e") + 1]
        assert (desktop / "Keep.app" / "Contents" / "Resources" / "applet.icns").read_bytes() == b"icns"

    def test_the_stock_icon_catalog_is_removed_and_the_bundle_resigned(self, tmp_path):
        """Finder shows the asset catalog's icon over applet.icns while the catalog is there."""
        desktop, icon = tmp_path / "Desktop", tmp_path / "keep.icns"
        desktop.mkdir()
        icon.write_bytes(b"icns")
        calls = []
        DesktopShortcut.ensure(desktop, tmp_path, icon, run=_osacompile(calls))
        app = desktop / "Keep.app"
        assert not (app / "Contents" / "Resources" / "Assets.car").exists()
        assert ["plutil", "-remove", "CFBundleIconName", str(app / "Contents" / "Info.plist")] in calls
        assert ["codesign", "--force", "-s", "-", str(app)] in calls

    def test_a_path_with_quotes_is_escaped_in_the_applescript_literal(self, tmp_path):
        source = DesktopShortcut._applescript(tmp_path / 'My "Keep"' / "keep.sh")
        assert source.startswith("do shell script quoted form of ")
        assert '\\"Keep\\"' in source

    def test_an_existing_applet_is_left_alone(self, tmp_path):
        desktop = tmp_path / "Desktop"
        (desktop / "Keep.app").mkdir(parents=True)
        calls = []
        assert DesktopShortcut.ensure(desktop, tmp_path, tmp_path / "none.icns", run=_osacompile(calls)) is True
        assert calls == []

    def test_no_desktop_folder_builds_nothing(self, tmp_path):
        calls = []
        assert DesktopShortcut.ensure(tmp_path / "Desktop", tmp_path, tmp_path / "none.icns",
                                      run=_osacompile(calls)) is False
        assert calls == []

    def test_a_failed_compile_is_reported_as_not_built(self, tmp_path):
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        assert DesktopShortcut.ensure(desktop, tmp_path, tmp_path / "none.icns",
                                      run=_osacompile([], returncode=1, make_bundle=False)) is False

    def test_a_missing_icon_still_builds_the_applet(self, tmp_path):
        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        assert DesktopShortcut.ensure(desktop, tmp_path, tmp_path / "none.icns", run=_osacompile([])) is True
        assert not (desktop / "Keep.app" / "Contents" / "Resources" / "applet.icns").exists()

    def test_the_shipped_icon_is_an_icns_file(self):
        assert DesktopShortcut.ICON.read_bytes()[:4] == b"icns"
