"""The legacy /stream wire format: how one request's AssistantEvents become the bytes the
iOS app, the Android outposts and the prompt test suite already understand.
Serialization only — every policy decision is a ChannelProfile field or a RequestContext
flag.

Modes (all pinned by tests/unit/interfaces/http/test_routes.py):
  text/plain  (ctx.text_only or ctx.server_sound)
    - streams_tokens channels (TextChat, Telegram): every Token as text
    - other channels (VoiceToVoice text=true — the prompt suite): tokens swallowed, the
      round's raw LLM text at RoundEnd (text_only only; server_sound rounds are silent)
    - ROUND_DELIMITER after every round when ctx.text_only
    - Done.full_text when ctx.server_sound (the reply text after the speaker finished)
  audio/mpeg  (client plays the reply): AudioChunk bytes
  Notice text is emitted verbatim in every mode.

The in-process Telegram bot no longer comes through here; an external client that still
sends source=Telegram gets the plain TextChat rendering (no keep-alive bytes).
"""
from typing import Generator, Iterable

from assistant.AssistantEvents import AssistantEvent, AudioChunk, Done, Notice, RoundEnd, Token
from assistant.ChannelProfile import SOURCE_TELEGRAM, profile_for
from common.logging import info, trace

# Separates LLM rounds so a text client can split each round into its own message.
ROUND_DELIMITER = "\x1e"

TEXT_HEADERS = {'Content-Type': 'text/plain; charset=utf-8',
                'Cache-Control': 'no-cache, no-store, must-revalidate'}
AUDIO_HEADERS = {'Content-Type': 'audio/mpeg', 'Accept-Ranges': 'none',
                 'Cache-Control': 'no-cache, no-store, must-revalidate'}


def response_headers(ctx) -> dict:
    return dict(TEXT_HEADERS if (ctx.text_only or ctx.server_sound) else AUDIO_HEADERS)


_telegram_over_http_reported = False


def render(events: Iterable[AssistantEvent], ctx) -> Generator[bytes | str, None, None]:
    """Turn an event stream into legacy wire chunks for the channel `ctx.source`."""
    global _telegram_over_http_reported
    profile = profile_for(ctx.source)
    if ctx.source == SOURCE_TELEGRAM:
        message = "HTTP /stream request with source=Telegram: the bot runs in-process now, rendering as plain text."
        if _telegram_over_http_reported:
            trace(message)
        else:
            _telegram_over_http_reported = True
            info(message)
    for event in events:
        if isinstance(event, AudioChunk):
            yield event.data
        elif isinstance(event, Token):
            if profile.streams_tokens:
                yield event.text
        elif isinstance(event, RoundEnd):
            if ctx.text_only:
                if not profile.streams_tokens and event.raw_text:
                    yield event.raw_text
                yield ROUND_DELIMITER
        elif isinstance(event, Notice):
            yield event.text
        elif isinstance(event, Done):
            if ctx.server_sound:
                yield event.full_text
