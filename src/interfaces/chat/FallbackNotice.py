"""DialogManager's fallback notice — "(Fable refused this request, had to use Opus
fallback)", appended to a reply after the fallback model answered — is a debugging aid,
not conversation. The console's Home tab draws it only with `^d debug` on; every other chat
follows that same switch, the console's persisted flag, so the two never disagree."""
import re

from interfaces.console import TUIState

PATTERN = re.compile(r"\s*\([^()\n]+ refused this request, had to use [^()\n]+ fallback\)")


def console_debug() -> bool:
    return bool(TUIState.load("home").get("show_debug", False))


def strip(text: str) -> str:
    return PATTERN.sub("", text)


def visible(text: str) -> str:
    """`text` with the notice removed unless the console's debug toggle is on."""
    if console_debug() or PATTERN.search(text) is None:
        return text
    return strip(text)
