from datetime import datetime, timezone
from unittest.mock import patch, Mock

import pytest

from assistant.integration.EmailProvider import _is_junk_media, _analyze_media
from integrations.gmail.GmailIntegration import GmailAccess
from assistant.tools import PdfExtractor


def _make_minimal_pdf(text: str) -> bytes:
    """Create a minimal valid PDF with extractable text (no external libs needed)."""
    content_stream = f"BT /F1 12 Tf 100 700 Td ({text}) Tj ET"
    stream_bytes = content_stream.encode("latin-1")
    lines = [
        b"%PDF-1.4",
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj",
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj",
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj",
        b"4 0 obj<</Length " + str(len(stream_bytes)).encode() + b">>stream\n"
        + stream_bytes + b"\nendstream\nendobj",
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj",
    ]
    body = b"\n".join(lines)
    xref_offset = len(body) + 1
    xref = b"\nxref\n0 6\n"
    xref += b"0000000000 65535 f \n"
    offset = len(b"%PDF-1.4\n")
    for line in lines[1:]:
        xref += f"{offset:010d} 00000 n \n".encode()
        offset += len(line) + 1
    trailer = b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n" + str(xref_offset).encode() + b"\n%%EOF"
    return body + xref + trailer


def _make_attachment(filename="file.pdf", content_type="application/pdf",
                     payload=b"data", size=50000, content_disposition="attachment",
                     content_id=""):
    att = Mock()
    att.filename = filename
    att.content_type = content_type
    att.payload = payload
    att.size = size
    att.content_disposition = content_disposition
    att.content_id = content_id
    return att


def _make_message(uid="123", from_="sender@test.com", to=("me@test.com",),
                  subject="Test", date=None, text="Body", html="",
                  attachments=None):
    msg = Mock()
    msg.uid = uid
    msg.from_ = from_
    msg.to = to
    msg.subject = subject
    msg.date = date or datetime(2024, 3, 15, 10, 30, tzinfo=timezone.utc)
    msg.text = text
    msg.html = html
    msg.attachments = attachments or []
    return msg


@pytest.mark.unit
class TestIsJunkImage:

    def test_small_image_is_junk(self):
        att = _make_attachment(content_type="image/png", size=5000)
        assert _is_junk_media(att) is True

    def test_inline_with_content_id_is_junk(self):
        att = _make_attachment(
            content_type="image/jpeg", size=20000,
            content_disposition="inline", content_id="<img001>"
        )
        assert _is_junk_media(att) is True

    def test_unsupported_image_type_is_junk(self):
        att = _make_attachment(content_type="image/svg+xml", size=20000)
        assert _is_junk_media(att) is True

    def test_large_attached_jpeg_is_not_junk(self):
        att = _make_attachment(
            content_type="image/jpeg", size=500000,
            content_disposition="attachment", content_id=""
        )
        assert _is_junk_media(att) is False

    def test_inline_without_content_id_is_not_junk(self):
        att = _make_attachment(
            content_type="image/png", size=50000,
            content_disposition="inline", content_id=""
        )
        assert _is_junk_media(att) is False


@pytest.mark.unit
class TestAnalyzeMedia:

    @patch("assistant.integration.EmailProvider.media_analyzer")
    def test_video_attachment_uses_video_label(self, mock_accessor):
        mock_ma = mock_accessor.return_value
        mock_ma.describe_bytes.return_value = "Video description: [A dog] A dog runs."

        result = _analyze_media(b"mp4", "video/mp4", "clip.mp4")
        assert result == "Video description: [A dog] A dog runs."
        kwargs = mock_ma.describe_bytes.call_args[1]
        assert kwargs["mime_type"] == "video/mp4"
        assert kwargs["context"] == "Video attached to an email, filename: clip.mp4"
        assert kwargs["fallback"] == "[Video]"

@pytest.mark.unit
class TestFormatEmailBodyWithAttachments:

    @patch("assistant.integration.EmailProvider.PdfExtractor")
    def test_pdf_attachment_included(self, mock_pdf):
        mock_pdf.extract_text.return_value = "--- Page 1 ---\nHello from PDF"
        provider = GmailAccess()
        att = _make_attachment(filename="report.pdf", content_type="application/pdf",
                               payload=b"pdf_bytes", size=100000)
        msg = _make_message(attachments=[att])
        result = provider._format_email_body(msg)
        assert "report.pdf" in result
        assert "Hello from PDF" in result
        mock_pdf.extract_text.assert_called_once_with(b"pdf_bytes")

    @patch("assistant.integration.EmailProvider._is_junk_media")
    def test_junk_image_skipped(self, mock_junk):
        mock_junk.return_value = True
        provider = GmailAccess()
        att = _make_attachment(filename="logo.png", content_type="image/png", size=3000)
        msg = _make_message(attachments=[att])
        result = provider._format_email_body(msg)
        assert "logo.png" not in result

    @patch("assistant.integration.EmailProvider._analyze_media")
    def test_video_attachment_analyzed(self, mock_analyze):
        mock_analyze.return_value = "Video description: [A dog] A dog runs on a beach."
        provider = GmailAccess()
        att = _make_attachment(filename="clip.mp4", content_type="video/mp4",
                               payload=b"mp4_bytes", size=300000)
        msg = _make_message(attachments=[att])
        result = provider._format_email_body(msg)
        assert "--- Attachment: clip.mp4 (video, 293 KB) ---" in result
        assert "A dog runs on a beach." in result
        mock_analyze.assert_called_once_with(b"mp4_bytes", "video/mp4", "clip.mp4")

    def test_video_attachment_is_not_junk(self):
        att = _make_attachment(content_type="video/quicktime", size=500000)
        assert _is_junk_media(att) is False

    def test_unsupported_attachment_skipped(self):
        provider = GmailAccess()
        att = _make_attachment(filename="data.zip", content_type="application/zip", size=500000)
        msg = _make_message(attachments=[att])
        result = provider._format_email_body(msg)
        assert "data.zip" not in result


@pytest.mark.unit
class TestPdfExtractor:

    def test_extract_from_valid_pdf(self):
        pdf_bytes = _make_minimal_pdf("Hello from test PDF")
        result = PdfExtractor.extract_text(pdf_bytes)
        assert "Hello from test PDF" in result
        assert "Page 1" in result

    def test_empty_pdf_returns_error(self):
        result = PdfExtractor.extract_text(b"not a pdf")
        assert result.startswith("ERROR:")

    def test_oversized_pdf_rejected(self):
        huge = b"x" * (PdfExtractor.MAX_PDF_SIZE + 1)
        result = PdfExtractor.extract_text(huge)
        assert "too large" in result.lower()
