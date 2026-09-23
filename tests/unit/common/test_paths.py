"""common.Paths must anchor on the server root even though the packages live under src/."""
from common import Paths


def test_aiserver_dir_is_the_server_root():
    assert (Paths.AISERVER_DIR / "floxy_ai_server.py").is_file()
