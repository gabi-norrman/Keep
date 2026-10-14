"""The VOICES section: carried by the voice toolkit, so it reaches the LLM on the voice channel
only and only while a voice is open, and gated by the voice interface's switch — an install
without voice never builds it."""
from datetime import date

import pytest

from assistant import VoiceCandidates
from assistant.commands import Toolkits
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
def test_the_section_is_gated_by_the_voice_interface_switch_and_carried_by_the_voice_toolkit():
    assert VoicesSectionGenerator.requires == "interfaces.voice.enabled"
    assert Toolkits.CARRIED_SECTIONS["voices"] == "voice", "the list goes out with the voice toolkit, not the prompt"
    assert not VoicesSectionGenerator.heading, "a carried section has no heading of its own in the prompt"
    assert "voices" in PromptManager.SECTION_ORDER, "a section has a pinned place"
    data = Toolkits.render_sections(Toolkits.by_name("voice"), {"voices": "- `voice-kfmq`: 120 clips"})
    assert data.startswith("## VOICES") and "voice-kfmq" in data
    assert Toolkits.render_sections(Toolkits.by_name("voice"), {"voices": ""}) == "", "no open voice, no block"


@pytest.mark.unit
def test_an_open_voice_is_listed_and_a_house_with_none_has_no_section(tmp_path, candidates):
    gen = VoicesSectionGenerator(prompt_manager=None)
    assert gen._fetch_content() == "", "nothing open: the toolkit brings no VOICES block"
    c = _file(tmp_path, candidates)
    content = gen._fetch_content()
    assert f"`{c.id}`" in content and "120 clips" in content
    (tmp_path / "voice_id").mkdir()
    assert VoiceCandidates.name(c.id, "Charlotte", directory=candidates, voice_id_dir=tmp_path / "voice_id",
                                log_dir=tmp_path / "log") == ""
    assert gen._fetch_content() == ""

