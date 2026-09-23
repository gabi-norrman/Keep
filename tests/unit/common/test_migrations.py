from unittest.mock import patch

import pytest

from common import Migrations


@pytest.mark.unit
class TestMigrations:
    @pytest.fixture(autouse=True)
    def tree(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Migrations, "AISERVER_DIR", tmp_path)
        with patch.object(Migrations.ConfigStore, "rename_keys", return_value=[]) as self.rename_keys:
            yield tmp_path

    def test_telegram_session_moves_to_its_new_name(self, tree):
        (tree / "telegram_monitor.session").write_text("auth")
        Migrations.run()
        assert not (tree / "telegram_monitor.session").exists()
        assert (tree / "telegram_feed.session").read_text() == "auth"

    def test_existing_new_session_is_never_overwritten(self, tree):
        (tree / "telegram_monitor.session").write_text("old")
        (tree / "telegram_feed.session").write_text("new")
        Migrations.run()
        assert (tree / "telegram_feed.session").read_text() == "new"
        assert (tree / "telegram_monitor.session").read_text() == "old"

    def test_a_failing_config_rename_still_moves_the_files(self, tree):
        self.rename_keys.side_effect = RuntimeError("boom")
        (tree / "telegram_monitor.session").write_text("auth")
        Migrations.run()
        assert (tree / "telegram_feed.session").exists()
