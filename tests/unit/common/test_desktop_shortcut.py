"""The Desktop shortcut: Keep.app on the Desktop running keep.sh."""
from common import DesktopShortcut

from tests.unit.common.test_applet import osacompile


class TestEnsure:
    def test_builds_keep_app_on_the_desktop_running_keep_sh(self, tmp_path):
        desktop, repo = tmp_path / "Desktop", tmp_path / "Keep"
        desktop.mkdir()
        calls = []
        assert DesktopShortcut.ensure(desktop, repo, tmp_path / "none.icns", run=osacompile(calls)) is True
        argv = calls[0]
        assert argv[argv.index("-o") + 1] == str(desktop / "Keep.app")
        assert str(repo / "keep.sh") in argv[argv.index("-e") + 1]

    def test_no_desktop_folder_builds_nothing(self, tmp_path):
        calls = []
        assert DesktopShortcut.ensure(tmp_path / "Desktop", tmp_path, tmp_path / "none.icns",
                                      run=osacompile(calls)) is False
        assert calls == []
