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
    """A stand-in for subprocess.run that records the argv and lays out the applet the way
    osacompile does (Contents/Resources inside the -o bundle)."""
    def run(argv, **kwargs):
        calls.append(argv)
        if make_bundle:
            (Path(argv[argv.index("-o") + 1]) / "Contents" / "Resources").mkdir(parents=True)
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
