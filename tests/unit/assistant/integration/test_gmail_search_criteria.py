import pytest

from assistant.integration.integrations.email.GmailIntegration import GmailAccess


@pytest.fixture
def gmail():
    return GmailAccess()


@pytest.mark.unit
class TestBuildSearchCriteria:

    def test_plain_ascii_query(self, gmail):
        criteria = gmail._build_search_criteria("invoice")
        key = str(criteria)
        assert 'X-GM-RAW' in key
        assert 'invoice' in key

    def test_diacritics_folded_to_ascii(self, gmail):
        # Gmail rejects raw 8-bit bytes on the SEARCH command line, so the
        # criteria must always be pure ASCII (see _build_search_criteria).
        criteria = gmail._build_search_criteria("café résumé")
        key = str(criteria)
        assert 'cafe resume' in key
        assert key.isascii()

    def test_swedish_diacritics_folded(self, gmail):
        criteria = gmail._build_search_criteria("Björkängen")
        key = str(criteria)
        assert 'Bjorkangen' in key
        assert key.isascii()

    def test_non_decomposing_letters_folded(self, gmail):
        criteria = gmail._build_search_criteria("Ørsted Straße Ægir")
        key = str(criteria)
        assert 'Orsted Strasse AEgir' in key
        assert key.isascii()

    def test_quotes_are_escaped(self, gmail):
        criteria = gmail._build_search_criteria('say "hello"')
        key = str(criteria)
        assert r'\"hello\"' in key
        assert 'say' in key

    def test_backslashes_are_escaped(self, gmail):
        criteria = gmail._build_search_criteria(r'path\to\file')
        key = str(criteria)
        assert r'path\\to\\file' in key

    def test_mixed_escaping(self, gmail):
        criteria = gmail._build_search_criteria(r'he said "go\" now"')
        key = str(criteria)
        assert r'\\' in key
        assert r'\"' in key

    def test_unfoldable_chars_dropped_when_query_survives(self, gmail):
        criteria = gmail._build_search_criteria("meeting 🎉")
        key = str(criteria)
        assert 'meeting' in key
        assert '🎉' not in key
        assert key.isascii()

    def test_fully_unfoldable_query_kept_verbatim(self, gmail):
        # Dropping everything would yield an empty X-GM-RAW that matches the
        # whole mailbox; better to send it as-is and let Gmail's BAD reply
        # surface to the LLM.
        criteria = gmail._build_search_criteria("日本語")
        key = str(criteria)
        assert '日本語' in key

    def test_no_implicit_date_filter(self, gmail):
        # Search must cover the full mailbox history — a silent SINCE window
        # made old emails (e.g. year-old order confirmations) unfindable.
        criteria = gmail._build_search_criteria("test")
        key = str(criteria)
        assert 'X-GM-RAW' in key
        assert 'SINCE' not in key
