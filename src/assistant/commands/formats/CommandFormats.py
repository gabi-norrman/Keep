"""The command format this process runs with: one choice, shared by every parser of the LLM's replies.

The chat tier's LLM decides (`AbstractLLM.command_format()`): the `DialogManager` hands its
class to `set_active` when it builds the smart LLM, and until then — and for a backend that
names none — the Keep syntax is the format. A change is announced to the listeners
(`on_change`), which is how the `PromptManager` re-renders a prompt it assembled before the
LLM was built.
"""
import inspect
import threading
import weakref
from typing import Callable

from common.logging import info, trace

from .CommandFormat import CommandFormat
from .DsmlFormat import DsmlFormat
from .InvokeFormat import InvokeFormat
from .KeepFormat import KeepFormat
from .QwenFormat import QwenFormat


class CommandFormats:
    """Names the active command format: the class a caller builds its parser from, with the
    command names it knows (`CommandFormats.active()(names)`)."""

    # Every format by the name a tier's `command_format:` config key may hold.
    BY_NAME: dict[str, type[CommandFormat]] = {cls.name: cls for cls in (KeepFormat, QwenFormat, DsmlFormat, InvokeFormat)}

    _active: type[CommandFormat] = KeepFormat
    _lock = threading.Lock()
    _listeners: list = []

    @classmethod
    def active(cls) -> type[CommandFormat]:
        return cls._active

    @classmethod
    def by_name(cls, name: str) -> type[CommandFormat] | None:
        """The format class called `name` (`keep`, `qwen`, `dsml`, `invoke`; case and surrounding blanks
        ignored), None for any other text."""
        return cls.BY_NAME.get(str(name).strip().lower())

    @classmethod
    def set_active(cls, fmt, reason: str = "") -> bool:
        """Make `fmt` (a `CommandFormat` class) the process's format; `reason` says who chose
        it for the log. Anything that is not a format class — a test double's return value —
        is ignored and the format stays. True when the format changed."""
        if not (isinstance(fmt, type) and issubclass(fmt, CommandFormat)):
            trace(f"Command format: {fmt!r} is not a format class, keeping {cls._active.name}")
            return False
        with cls._lock:
            if fmt is cls._active:
                return False
            previous, cls._active = cls._active, fmt
            listeners = [ref() for ref in cls._listeners]
        info(f"Command format: {fmt.name} (was {previous.name}){f' — chosen by {reason}' if reason else ''}")
        for listener in listeners:
            if listener is not None:
                listener(fmt)
        return True

    @classmethod
    def on_change(cls, callback: Callable[[type[CommandFormat]], None]) -> None:
        """Call `callback(format class)` after every change; held weakly, so a bound method
        of an object that is gone is simply dropped."""
        ref = weakref.WeakMethod(callback) if inspect.ismethod(callback) else weakref.ref(callback)
        with cls._lock:
            cls._listeners = [r for r in cls._listeners if r() is not None] + [ref]

    @classmethod
    def reset(cls) -> None:
        """Back to the Keep format, listeners dropped, nobody told (tests)."""
        with cls._lock:
            cls._active = KeepFormat
            cls._listeners = []
