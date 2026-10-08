import json
from unittest.mock import Mock, patch

import pytest

from assistant.llm.AbstractLLM import LLMUnavailable
from integrations.photos.PhotoContext import PhotoContext, EXTRACT_PROMPT, NO_MEMORY

BANK = "# Family\nMarcus (owner) and Charlotte. The cat is Mittens.\n# Rules\nNever wake anyone before seven."
EXTRACT = "- Marcus, Charlotte\n- cat Mittens"


@pytest.mark.unit
class TestPhotoContext:
    """One extraction per bank version, served from memory and a cache file afterwards; a
    failed extraction costs this photo the whole bank and nothing more."""

    @pytest.fixture(autouse=True)
    def _cache_file(self, tmp_path):
        PhotoContext._memo = None
        PhotoContext._llm = None
        with patch.object(PhotoContext, 'CACHE_PATH', tmp_path / "photos" / "photo_context.json"):
            yield
        PhotoContext._memo = None
        PhotoContext._llm = None

    @staticmethod
    def _llm(reply=EXTRACT):
        llm = Mock()
        llm.think.return_value = reply
        PhotoContext._llm = llm
        return llm

    def test_the_extract_is_made_once_and_the_bank_is_the_input(self):
        llm = self._llm()
        assert PhotoContext.briefing(BANK) == EXTRACT
        assert PhotoContext.briefing(BANK) == EXTRACT
        llm.think.assert_called_once_with(system_message=EXTRACT_PROMPT, user_message=BANK)

    def test_the_extraction_runs_on_the_smart_tier_not_the_indexer(self):
        """Reading a whole bank for what tells people and places apart is the curator's kind
        of work; the cheap indexing model is never asked, and the one-shot call is not cached."""
        with patch('integrations.photos.PhotoContext.parse_llm_config', return_value=("ClaudeLLM", "m")) as tier, \
             patch('integrations.photos.PhotoContext.create_llm', return_value=self._llm()) as build:
            PhotoContext._llm = None
            assert PhotoContext.briefing(BANK) == EXTRACT
        tier.assert_called_once_with('smart')
        build.assert_called_once_with("ClaudeLLM", "m", prompt_cache=False)

    def test_a_restart_reads_the_extract_from_the_cache_file(self):
        self._llm()
        PhotoContext.briefing(BANK)
        PhotoContext._memo = None    # a fresh process
        llm = self._llm("a different answer")
        assert PhotoContext.briefing(BANK) == EXTRACT
        llm.think.assert_not_called()
        on_disk = json.loads(PhotoContext.CACHE_PATH.read_text())
        assert on_disk["extract"] == EXTRACT

    def test_a_changed_bank_is_extracted_anew(self):
        llm = self._llm()
        PhotoContext.briefing(BANK)
        llm.think.return_value = "- Marcus, Charlotte, Frances\n- cat Mittens"
        assert "Frances" in PhotoContext.briefing(BANK + "\nFrances was born in 2026.")
        assert llm.think.call_count == 2

    def test_an_empty_bank_needs_no_call(self):
        llm = self._llm()
        assert PhotoContext.briefing("  \n") == NO_MEMORY
        llm.think.assert_not_called()

    def test_a_failed_extraction_falls_back_to_the_bank_and_is_tried_again(self):
        llm = self._llm()
        llm.think.side_effect = [RuntimeError("boom"), "", EXTRACT]
        assert PhotoContext.briefing(BANK) == BANK
        assert PhotoContext.briefing(BANK) == BANK, "an empty answer is no extract either"
        assert PhotoContext.briefing(BANK) == EXTRACT
        assert not PhotoContext.CACHE_PATH.parent.exists() or \
            json.loads(PhotoContext.CACHE_PATH.read_text())["extract"] == EXTRACT

    def test_the_smart_tiers_outage_is_not_the_indexers(self):
        """The extraction's provider being out says nothing about the indexing backend: the
        photo gets the whole bank and is indexed, nothing propagates to the feed."""
        llm = self._llm()
        llm.think.side_effect = LLMUnavailable("out of credits")
        assert PhotoContext.briefing(BANK) == BANK

    def test_an_unreadable_cache_file_is_extracted_over(self):
        PhotoContext.CACHE_PATH.parent.mkdir(parents=True)
        PhotoContext.CACHE_PATH.write_text("{not json")
        llm = self._llm()
        assert PhotoContext.briefing(BANK) == EXTRACT
        llm.think.assert_called_once()
