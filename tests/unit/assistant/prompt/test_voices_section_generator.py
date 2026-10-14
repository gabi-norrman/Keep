"""The VOICES section: in the prompt's data block only while a voice is open, and gated by the
voice interface's switch — an install without voice never carries it."""
from datetime import date

import pytest

from assistant import VoiceCandidates
from assistant.prompt.PromptManager import PromptManager
from assistant.prompt.sections.VoicesSectionGenerator import VoicesSectionGenerator


@pytest.fixture
def candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(VoiceCandidates, "CANDIDATES_DIR", tmp_path / "candidates")
    return tmp_path / "candidates"


def _file(tmp_path, candidates):
    sample = tmp_path / "what_s_for_dinner_1.wav"
    sample.write_bytes(b"RIFF")
    return VoiceCandidates.create(120, 4, "2026-10-08", "2026-10-11", "mostly 17–20h", ["what s for dinner"],
                                  [1.0, 0.0], [sample], directory=candidates, today=date(2026, 10, 12))


@pytest.mark.unit
def test_the_section_is_gated_by_the_voice_interface_switch_and_stands_in_the_data_block():
    assert VoicesSectionGenerator.requires == "interfaces.voice.enabled"
    assert VoicesSectionGenerator.heading, "a heading places it in {INTEGRATION_DATA}, left out when empty"
    assert "voices" in PromptManager.SECTION_ORDER, "a block section has a pinned place"


@pytest.mark.unit
def test_an_open_voice_is_listed_and_a_house_with_none_has_no_section(tmp_path, candidates):
    gen = VoicesSectionGenerator(prompt_manager=None)
    assert gen._fetch_content() == "", "nothing open: the data block leaves the heading out"
    c = _file(tmp_path, candidates)
    content = gen._fetch_content()
    assert f"`{c.id}`" in content and "120 clips" in content and "not been asked" in content
    VoiceCandidates.dismiss(c.id, "the radio", directory=candidates, log_dir=tmp_path / "log")
    assert gen._fetch_content() == ""

