"""Turns media the user sent into text the chat LLM can work with — the same for every
chat platform: voice notes are transcribed, videos are described by the video tier."""
from common.logging import info, trace


def transcribe_voice(data: bytes) -> str:
    """Speech-to-text for a voice note; "" when nothing came out."""
    from interfaces.telegram.GoogleCloudSTT import GoogleCloudSTT
    transcript = GoogleCloudSTT.get_instance().transcribe(data) or ""
    if transcript:
        trace(f"Voice note transcribed ({len(data)} bytes): {transcript!r}")
    else:
        info(f"Voice note ({len(data)} bytes) produced no transcript.")
    return transcript


def describe_video(data: bytes, mime_type: str, context: str) -> str:
    """A description of the clip from the video tier; "" when it could not be analysed."""
    from assistant.tools.VideoAnalyzer import video_analyzer
    description = video_analyzer().describe_bytes(data, mime_type=mime_type, context=context, fallback="") or ""
    if not description:
        info(f"Video ({len(data)} bytes, {mime_type}) could not be analysed.")
    return description
