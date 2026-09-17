"""The wording of a command echo's completion note: "done after 4s, received 345 chars"."""
import time


def describe_completion(started: float, result=None, failed: bool = False) -> str:
    """How the command ended, for the echo the user is watching. `result` is what the
    command handed back for the LLM (text, or a text/image block list); None for
    commands that return nothing. A command that finished before the clock could
    show anything ("0.0s") is just "done" — a config write is not worth timing."""
    elapsed = time.time() - started
    verb = "failed" if failed else "done"
    parts = [f"{verb} after {describe_duration(elapsed)}" if elapsed >= 0.05 else verb]
    received = describe_result(result)
    if received:
        parts.append(f"received {received}")
    return ", ".join(parts)


def describe_duration(seconds: float) -> str:
    """"0.3s" under a second, "4s" up to a minute, then "1m 12s" the way Claude Code counts."""
    if seconds < 1:
        return f"{seconds:.1f}s"
    whole = round(seconds)
    if whole < 60:
        return f"{whole}s"
    minutes, rest = divmod(whole, 60)
    return f"{minutes}m {rest}s"


def describe_result(result) -> str:
    if isinstance(result, str):
        return _chars(len(result)) if result.strip() else ""
    if isinstance(result, list):
        images = sum(1 for block in result if isinstance(block, dict) and block.get("type") == "image")
        chars = sum(len(block.get("text", "")) for block in result
                    if isinstance(block, dict) and block.get("type") == "text")
        parts = []
        if images:
            parts.append(f"{images} image{'s' if images != 1 else ''}")
        if chars:
            parts.append(_chars(chars))
        return ", ".join(parts)
    return ""


def _chars(n: int) -> str:
    return f"{n / 1000:.1f}k chars" if n >= 1000 else f"{n} chars"
