"""The indexing prompts' "thinking" field is dropped for a backend that refuses to be asked
for its reasoning (Anthropic's reasoning_extraction refusal) and kept for every other."""
from unittest.mock import patch

import pytest

from assistant.llm.AbstractLLM import AbstractLLM
from integrations.photos.PhotoProcessor import (ANALYSIS_PROMPT_SYSTEM, VIDEO_ANALYSIS_PROMPT_SYSTEM,
                                                PhotoProcessor, without_reasoning_field)


class _Backend(AbstractLLM):
    def __init__(self, refuses: bool):
        self._refuses = refuses
        self.video_system = None

    def name(self):
        return "Backend"

    def refuses_reasoning_requests(self):
        return self._refuses

    def think_with_video(self, system_message, user_message, video_bytes, mime_type, fps=None):
        self.video_system = system_message
        return '{"short_description": "a", "detailed_description": "b", "spoken_content": "", "pretty_score": 1, "family_importance_score": 2}'

    def create_completion(self, messages, thinking=False, volatile_tail=0):
        return ""

    def create_completion_with_images(self, messages, images, background=False):
        return ""

    def create_streaming_completion(self, messages, thinking=False, volatile_tail=0):
        yield ""

    def initialize(self):
        return True


def _processor(refuses: bool) -> PhotoProcessor:
    processor = PhotoProcessor()
    processor._llm = _Backend(refuses)
    return processor


@pytest.mark.parametrize("prompt", [ANALYSIS_PROMPT_SYSTEM, VIDEO_ANALYSIS_PROMPT_SYSTEM])
def test_only_the_thinking_field_goes(prompt):
    stripped = without_reasoning_field(prompt.format(current_memory="(none)"))
    assert '"thinking"' not in stripped
    for kept in ('"short_description"', '"detailed_description"', '"pretty_score"', '"family_importance_score"', "Scoring guidance"):
        assert kept in stripped
    assert stripped.count("\n") == prompt.format(current_memory="(none)").count("\n") - 1


def test_a_refusing_backend_gets_the_photo_prompt_without_the_field():
    with patch.object(PhotoProcessor, "_read_memory", return_value="Marcus likes boats"):
        assert '"thinking"' not in _processor(True)._photo_system_prompt()
        assert '"thinking"' in _processor(False)._photo_system_prompt()


def test_the_video_prompt_follows_the_same_verdict():
    for refuses in (True, False):
        processor = _processor(refuses)
        with patch.object(PhotoProcessor, "_read_memory", return_value=""):
            processor._analyze_video_with_llm(b"mp4", "video/mp4", "2026-10-19", "Home", "none", "none", "video", 12.0)
        assert ('"thinking"' in processor._llm.video_system) is not refuses


def test_the_verdict_is_asked_once_and_without_building_a_client():
    """Before the backend is built the question goes to an uninitialized instance of the
    configured one, the way the worker count is decided; the answer is then kept."""
    processor = PhotoProcessor()
    backend = _Backend(True)
    with patch("integrations.photos.PhotoProcessor.parse_llm_config", return_value=("X", "m")) as cfg, \
         patch("integrations.photos.PhotoProcessor.create_llm", return_value=backend) as build:
        assert processor._backend_refuses_reasoning() is True
        assert processor._backend_refuses_reasoning() is True
    assert cfg.call_count == 1 and build.call_count == 1
    assert processor._llm is None, "asking must not stand in for building the indexing LLM"


def test_a_backend_that_cannot_be_built_keeps_the_field():
    processor = PhotoProcessor()
    with patch("integrations.photos.PhotoProcessor.parse_llm_config", side_effect=RuntimeError("no config")):
        assert processor._backend_refuses_reasoning() is False
