import smtplib
from datetime import datetime, timezone
from unittest.mock import patch, Mock, MagicMock

import pytest

from assistant.integration.integrations.email.ICloudEmailIntegration import (
    ICloudEmailAccess,
    SEND_RATE_LIMIT_MAX,
)


def _make_message(uid="123", from_="sender@test.com", to=("me@test.com",),
                  subject="Test Subject", date=None, text="Hello body", html="",
                  flags=("\\Seen",)):
    msg = Mock()
    msg.uid = uid
    msg.from_ = from_
    msg.to = to
    msg.subject = subject
    msg.date = date or datetime(2024, 3, 15, 10, 30, tzinfo=timezone.utc)
    msg.text = text
    msg.html = html
    msg.attachments = []
    msg.flags = flags
    return msg


@pytest.fixture
def icloud():
    return ICloudEmailAccess()


@pytest.fixture
def mock_config():
    with patch('assistant.integration.integrations.email.ICloudEmailIntegration.ConfigManager') as MockCM:
        instance = MockCM.return_value
        instance.get.side_effect = lambda key: {
            "icloud_email.address": "user@icloud.com",
            "icloud_email.password": "icloud_app_pw"
        }[key]
        yield instance


@pytest.mark.unit
class TestResolveEmailId:

    def test_short_id_strips_prefix_and_resolves_from_cache(self, icloud):
        icloud._email_id_cache["AA"] = "real-uid-123"
        result = icloud._resolve_email_id("IC-AA")
        assert result == "real-uid-123"

@pytest.mark.unit
class TestStripHtml:

    def test_script_and_style_tags_stripped(self, icloud):
        html = "<html><head><style>body{}</style></head><body><script>alert(1)</script><p>Hello</p></body></html>"
        result = icloud.strip_html(html)
        assert "alert" not in result
        assert "body{}" not in result
        assert "Hello" in result

    def test_simple_p_content_extracted(self, icloud):
        html = "<p>Plain text content</p>"
        result = icloud.strip_html(html)
        assert result == "Plain text content"

    def test_anchor_href_preserved(self, icloud):
        html = '<p>Manage your booking: <a href="https://book.example.com/cancel/10203040">Cancel booking</a></p>'
        result = icloud.strip_html(html)
        assert '<a href="https://book.example.com/cancel/10203040">Cancel booking</a>' in result

    def test_anchor_without_text_falls_back_to_href(self, icloud):
        html = '<a href="https://book.example.com/manage"><img src="cid:btn"></a>'
        result = icloud.strip_html(html)
        assert "https://book.example.com/manage" in result

    def test_mailto_anchor_href_preserved(self, icloud):
        html = '<p>Questions? <a href="mailto:support@example.com">Email support</a></p>'
        result = icloud.strip_html(html)
        assert '<a href="mailto:support@example.com">Email support</a>' in result

    def test_relative_and_anchor_only_links_dropped(self, icloud):
        html = '<a href="#top">Top</a> <a href="/foo">Foo</a>'
        result = icloud.strip_html(html)
        assert "href" not in result
        assert "Top" in result
        assert "Foo" in result


@pytest.mark.unit
class TestFormatEmailBody:

    def test_html_links_appended_when_text_body_lacks_them(self, icloud):
        msg = _make_message(
            text="Your booking is confirmed. See the email for details.",
            html='<a href="https://book.example.com/cancel/10203040">Cancel booking</a>',
        )
        result = icloud._format_email_body(msg)
        assert "https://book.example.com/cancel/10203040" in result

    def test_html_links_not_duplicated_when_already_in_text(self, icloud):
        url = "https://book.example.com/cancel/10203040"
        msg = _make_message(
            text=f"Cancel here: {url}",
            html=f'<a href="{url}">Cancel booking</a>',
        )
        result = icloud._format_email_body(msg)
        assert result.count(url) == 1

@pytest.fixture
def mock_smtp():
    with patch('assistant.integration.integrations.email.ICloudEmailIntegration.smtplib.SMTP') as MockSMTP:
        server = MagicMock()
        MockSMTP.return_value.__enter__.return_value = server
        MockSMTP.return_value.__exit__.return_value = False
        yield MockSMTP, server


@pytest.mark.unit
class TestICloudSend:

    def test_successful_send_returns_empty_string(self, icloud, mock_config, mock_smtp):
        import email as email_pkg
        MockSMTP, server = mock_smtp
        result = icloud.send("alice@example.com", "Hi", "Hello there")
        assert result == ""
        MockSMTP.assert_called_once()
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("user@icloud.com", "icloud_app_pw")
        server.sendmail.assert_called_once()
        from_addr, to_addrs, payload = server.sendmail.call_args[0]
        assert from_addr == "user@icloud.com"
        assert to_addrs == ["alice@example.com"]
        parsed = email_pkg.message_from_string(payload)
        assert parsed["Subject"] == "Hi"
        assert parsed["From"] == "user@icloud.com"
        assert parsed["To"] == "alice@example.com"
        assert parsed.get_payload(decode=True).decode("utf-8") == "Hello there"

    def test_empty_recipient_returns_error_without_smtp(self, icloud, mock_config, mock_smtp):
        MockSMTP, _ = mock_smtp
        result = icloud.send("", "subject", "body")
        assert result == "empty recipient"
        MockSMTP.assert_not_called()

    def test_empty_body_returns_error_without_smtp(self, icloud, mock_config, mock_smtp):
        MockSMTP, _ = mock_smtp
        result = icloud.send("a@b.com", "subject", "   ")
        assert result == "empty body"
        MockSMTP.assert_not_called()

    def test_invalid_recipient_returns_error_without_smtp(self, icloud, mock_config, mock_smtp):
        MockSMTP, _ = mock_smtp
        result = icloud.send("not-an-email", "subject", "body")
        assert "invalid recipient" in result
        MockSMTP.assert_not_called()

    def test_auth_error_returns_friendly_message(self, icloud, mock_config, mock_smtp):
        MockSMTP, server = mock_smtp
        server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad password")
        result = icloud.send("alice@example.com", "Hi", "body")
        assert "auth failed" in result.lower()
        server.sendmail.assert_not_called()

    def test_recipient_refused_returns_error(self, icloud, mock_config, mock_smtp):
        MockSMTP, server = mock_smtp
        server.sendmail.side_effect = smtplib.SMTPRecipientsRefused({"alice@example.com": (550, b"no such user")})
        result = icloud.send("alice@example.com", "Hi", "body")
        assert "recipient refused" in result
        assert "alice@example.com" in result

    def test_network_error_returns_error(self, icloud, mock_config, mock_smtp):
        MockSMTP, _ = mock_smtp
        MockSMTP.side_effect = TimeoutError("connect timed out")
        result = icloud.send("alice@example.com", "Hi", "body")
        assert "network error" in result.lower()

    def test_rate_limit_blocks_after_max_sends(self, icloud, mock_config, mock_smtp):
        MockSMTP, server = mock_smtp
        for _ in range(SEND_RATE_LIMIT_MAX):
            assert icloud.send("alice@example.com", "Hi", "body") == ""
        result = icloud.send("alice@example.com", "Hi", "body")
        assert "rate limit" in result
        assert MockSMTP.call_count == SEND_RATE_LIMIT_MAX
