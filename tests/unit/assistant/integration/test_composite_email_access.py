from unittest.mock import Mock, PropertyMock

import pytest

from assistant.integration.CompositeEmailAccess import CompositeEmailAccess
from assistant.integration.EmailProvider import EmailProvider


def _make_provider(name: str, id_prefix: str, fetch_result="", search_result="", read_result=""):
    provider = Mock(spec=EmailProvider)
    type(provider).name = PropertyMock(return_value=name)
    type(provider).id_prefix = PropertyMock(return_value=id_prefix)
    provider.fetch_latest.return_value = fetch_result
    provider.search.return_value = search_result
    provider.read.return_value = read_result
    provider.move.return_value = ""
    provider.list_folders.return_value = ""
    return provider


@pytest.mark.unit
class TestFetchLatest:

    def test_merges_results_from_multiple_providers(self):
        gmail = _make_provider("gmail", "GM", fetch_result="[GM-AA] Mar 15 | From: a@b.com | Subject: Hi")
        icloud = _make_provider("icloud", "IC", fetch_result="[IC-AA] Mar 16 | From: c@d.com | Subject: Hey")
        composite = CompositeEmailAccess([gmail, icloud])

        result = composite.fetch_latest(None)

        assert "--- Gmail ---" in result
        assert "--- Icloud ---" in result
        assert "[GM-AA]" in result
        assert "[IC-AA]" in result

    def test_forwards_unread_only_to_all_providers(self):
        gmail = _make_provider("gmail", "GM", fetch_result="emails")
        icloud = _make_provider("icloud", "IC", fetch_result="emails")
        composite = CompositeEmailAccess([gmail, icloud])

        composite.fetch_latest("@SaneLater", True)

        gmail.fetch_latest.assert_called_once_with("@SaneLater", True)
        icloud.fetch_latest.assert_called_once_with("@SaneLater", True)

    def test_single_provider_still_has_header(self):
        gmail = _make_provider("gmail", "GM", fetch_result="[GM-AA] email")
        composite = CompositeEmailAccess([gmail])

        result = composite.fetch_latest(None)

        assert "--- Gmail ---" in result
        assert "[GM-AA]" in result

    def test_error_in_one_provider_doesnt_break_other(self):
        gmail = _make_provider("gmail", "GM", fetch_result="[GM-AA] email")
        icloud = _make_provider("icloud", "IC")
        icloud.fetch_latest.side_effect = Exception("Connection failed")
        composite = CompositeEmailAccess([gmail, icloud])

        result = composite.fetch_latest(None)

        assert "[GM-AA]" in result
        assert "Error" in result
        assert "Connection failed" in result


@pytest.mark.unit
class TestSearch:

    def test_merges_search_results(self):
        gmail = _make_provider("gmail", "GM", search_result="[GM-AA] invoice")
        icloud = _make_provider("icloud", "IC", search_result="[IC-AA] invoice")
        composite = CompositeEmailAccess([gmail, icloud])

        result = composite.search("invoice")

        assert "[GM-AA]" in result
        assert "[IC-AA]" in result

@pytest.mark.unit
class TestRead:

    def test_routes_to_correct_provider_by_prefix(self):
        gmail = _make_provider("gmail", "GM", read_result="Gmail email body")
        icloud = _make_provider("icloud", "IC", read_result="iCloud email body")
        composite = CompositeEmailAccess([gmail, icloud])

        result = composite.read("GM-AA")

        assert result == "Gmail email body"
        gmail.read.assert_called_once_with("AA")
        icloud.read.assert_not_called()

    def test_no_prefix_tries_each_provider(self):
        gmail = _make_provider("gmail", "GM", read_result="Email with ID 42 not found.")
        icloud = _make_provider("icloud", "IC", read_result="Found email body")
        composite = CompositeEmailAccess([gmail, icloud])

        result = composite.read("42")

        assert result == "Found email body"

    def test_no_prefix_not_found_anywhere(self):
        gmail = _make_provider("gmail", "GM", read_result="Email with ID 42 not found.")
        icloud = _make_provider("icloud", "IC", read_result="Email with ID 42 not found.")
        composite = CompositeEmailAccess([gmail, icloud])

        result = composite.read("42")

        assert "not found" in result

    def test_strips_whitespace_from_prefixed_id(self):
        gmail = _make_provider("gmail", "GM", read_result="email body")
        composite = CompositeEmailAccess([gmail])

        composite.read("  GM-AA  ")

        gmail.read.assert_called_once_with("AA")

    def test_unrecognized_prefix_falls_through_to_sequential_scan(self):
        gmail = _make_provider("gmail", "GM", read_result="Email with ID XX-AA not found.")
        icloud = _make_provider("icloud", "IC", read_result="Found the email!")
        composite = CompositeEmailAccess([gmail, icloud])

        result = composite.read("XX-AA")

        assert result == "Found the email!"

    def test_section_ordering_matches_provider_insertion_order(self):
        gmail = _make_provider("gmail", "GM", fetch_result="gmail emails")
        icloud = _make_provider("icloud", "IC", fetch_result="icloud emails")
        composite = CompositeEmailAccess([gmail, icloud])

        result = composite.fetch_latest(None)

        gmail_pos = result.index("--- Gmail ---")
        icloud_pos = result.index("--- Icloud ---")
        assert gmail_pos < icloud_pos

    def test_all_providers_raise_exceptions_all_sections_have_errors(self):
        gmail = _make_provider("gmail", "GM")
        gmail.fetch_latest.side_effect = Exception("Gmail down")
        icloud = _make_provider("icloud", "IC")
        icloud.fetch_latest.side_effect = Exception("iCloud down")
        composite = CompositeEmailAccess([gmail, icloud])

        result = composite.fetch_latest(None)

        assert "Error" in result
        assert "Gmail down" in result
        assert "iCloud down" in result


@pytest.mark.unit
class TestMove:

    def test_routes_to_correct_provider_by_prefix(self):
        gmail = _make_provider("gmail", "GM")
        gmail.move.return_value = "Moved email AA to @SaneBlackHole."
        icloud = _make_provider("icloud", "IC")
        composite = CompositeEmailAccess([gmail, icloud])

        result = composite.move("GM-AA", "@SaneBlackHole")

        assert result == "Moved email AA to @SaneBlackHole."
        gmail.move.assert_called_once_with("AA", "@SaneBlackHole")
        icloud.move.assert_not_called()

    def test_unknown_prefix_returns_helpful_error(self):
        gmail = _make_provider("gmail", "GM")
        composite = CompositeEmailAccess([gmail])

        result = composite.move("42", "Archive")

        assert "Could not route" in result
        gmail.move.assert_not_called()


@pytest.mark.unit
class TestSubjectFor:

    def test_routes_to_correct_provider_by_prefix(self):
        gmail = _make_provider("gmail", "GM")
        gmail.subject_for.return_value = "Order confirmation"
        icloud = _make_provider("icloud", "IC")
        composite = CompositeEmailAccess([gmail, icloud])

        assert composite.subject_for("GM-AA") == "Order confirmation"
        gmail.subject_for.assert_called_once_with("AA")
        icloud.subject_for.assert_not_called()

    def test_no_prefix_returns_none(self):
        gmail = _make_provider("gmail", "GM")
        composite = CompositeEmailAccess([gmail])

        assert composite.subject_for("42") is None
        gmail.subject_for.assert_not_called()

    def test_unknown_prefix_returns_none(self):
        gmail = _make_provider("gmail", "GM")
        composite = CompositeEmailAccess([gmail])

        assert composite.subject_for("XX-AA") is None


class _FakeMessage:
    def __init__(self, uid, subject):
        self.uid = uid
        self.subject = subject
        self.date = None
        self.from_ = "sender@example.com"
        self.flags = ()


class _FakeProvider(EmailProvider):
    IMAP_HOST = "imap.example.com"
    IMAP_FOLDER = "INBOX"

    @property
    def name(self):
        return "fake"

    @property
    def id_prefix(self):
        return "FK"

    def _get_credentials(self):
        return ("user", "pass")


@pytest.mark.unit
class TestEmailProviderSubjectCache:

    def test_subject_cached_after_listing(self):
        provider = _FakeProvider()
        provider._format_email_list([_FakeMessage("100", "Hello there")])

        assert provider.subject_for("AA") == "Hello there"

    def test_subject_for_strips_own_prefix(self):
        provider = _FakeProvider()
        provider._format_email_list([_FakeMessage("100", "Hello there")])

        assert provider.subject_for("FK-AA") == "Hello there"

    def test_relisting_clears_stale_subjects(self):
        provider = _FakeProvider()
        provider._format_email_list([_FakeMessage("100", "First batch")])
        provider._format_email_list([_FakeMessage("200", "Second batch")])

        assert provider.subject_for("AA") == "Second batch"
