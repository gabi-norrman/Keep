"""common.Paths must anchor on the server root even though the packages live under src/."""
from common import Paths


def test_aiserver_dir_is_the_server_root():
    assert (Paths.AISERVER_DIR / "floxy_ai_server.py").is_file()


def test_derived_dirs_hang_off_aiserver_dir():
    assert Paths.SRC_DIR == Paths.AISERVER_DIR / "src"
    assert (Paths.SRC_DIR / "common" / "Paths.py").is_file()
    assert Paths.RES_DIR == Paths.AISERVER_DIR / "res"
    assert Paths.CACHE_DIR == Paths.AISERVER_DIR / "cache"
    assert Paths.LOG_DIR == Paths.AISERVER_DIR / "log"
    assert Paths.VOICE_ID_DIR == Paths.AISERVER_DIR / "voice_id"
    assert Paths.CONFIG_PATH == Paths.AISERVER_DIR / "config.yaml"
    assert Paths.VAULT_PATH == Paths.AISERVER_DIR / "vault.bin"
    assert Paths.REPO_DIR == Paths.AISERVER_DIR
    assert (Paths.RES_DIR / "prompt.md").is_file()
