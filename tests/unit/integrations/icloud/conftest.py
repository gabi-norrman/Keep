import pytest


@pytest.fixture(autouse=True)
def icloud_session_dir(tmp_path):
    """The session folder, per test: a refused sign-in parks the integration in a file there
    (ICloudIntegration.PARK_FILE), and a test's park must never land in the real cache."""
    from unittest.mock import patch
    with patch('integrations.icloud.ICloudIntegration.ICLOUD_SESSION_DIR', tmp_path / "icloud_session"):
        yield tmp_path / "icloud_session"
