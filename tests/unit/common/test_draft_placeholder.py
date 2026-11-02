import pytest

from common.DraftPlaceholder import SPINNER_FRAMES_BRAILLE, SPINNER_FRAMES_C64, is_draft_placeholder


@pytest.mark.unit
class TestIsDraftPlaceholder:
    """What a draft bubble reads as before its text arrives, in the HTML the transport sends and
    in the Markdown Telethon renders the same message as (a `<code>` span comes back in backticks)."""

    @pytest.mark.parametrize("text", [
        "<code>█</code>", "<code>░</code>", "`█`", "`░`", "█", " ░ \n",
        "Thinking…\n <code>█</code>", "Thinking…\n `░`",
        "Thinking (↓ 1.2k tokens · 45 t/s)…\n `█`",
        "Prefilling (43%)…\n <code>█</code>", "Prefilling (43% · 1.5k t/s)…\n `░`",
        "Running command…\n `█`", "Running commands…\n <code>░</code>",
        "`⠋`", "Thinking…\n ⠹",
    ])
    def test_a_frame_with_or_without_its_phase_line_is_a_placeholder(self, text):
        assert is_draft_placeholder(text) is True

    @pytest.mark.parametrize("text", [
        "", "   ", "\n", None,
        "Hi there", "█ nope", "`█` and then some", "Thinking…",
        "Any line over it…\n `█`", "Thinking…\n `█`\n Done", "Thinking (unclosed…\n `█`",
        "code: `x`",
    ])
    def test_anything_with_words_in_it_is_a_message(self, text):
        assert is_draft_placeholder(text) is False

    def test_every_frame_of_both_spinner_styles_is_known(self):
        for frame in SPINNER_FRAMES_C64 + SPINNER_FRAMES_BRAILLE:
            assert is_draft_placeholder(f"<code>{frame}</code>"), frame
