import pytest


@pytest.fixture(autouse=True)
def icloud_session_dir(tmp_path):
    """The session folder, per test: a refused sign-in parks the integration in a file there
    (ICloudIntegration.PARK_FILE), and a test's park must never land in the real cache."""
    from unittest.mock import patch
    with patch('integrations.icloud.ICloudIntegration.ICLOUD_SESSION_DIR', tmp_path / "icloud_session"):
        yield tmp_path / "icloud_session"


@pytest.fixture(autouse=True)
def live_sms_reader():
    """The auto-2FA flow requests a code on a timer only while something reads the Mac's text
    messages (SmsCodeReader.live); the tests of that flow run with a live reader unless they
    patch it away themselves."""
    from unittest.mock import patch
    with patch('integrations.icloud.ICloudIntegration.SmsCodeReader.live', return_value=["iMessage"]):
        yield
