"""How a system history row reads in the Home tab chat.

The rows are stored the way the LLM sees them, as one flat string. Messaging traffic
(`iMessage received:\nFrom Sam to Alex: …`) reads poorly that way, so the feed
marks it up for the pane: the parties in bold ahead of the body, the channel line dropped
since the header already names it. A media message reads like a text message first — the
parties and the text on one line — then the bracketed short description on a line of its own,
and the full description under that in a class the pane tones down. Every other row is escaped
and shown as it is.
"""
import html
import re

_MESSAGE_RE = re.compile(r"^(iMessage|Telegram) (received|sent):\n(?:From )?(.+?)(?: to (.+?))?: (.*)$", re.DOTALL)
_DESCRIPTION_RE = re.compile(r"^(?:Video|Image) description: (\[.+?\]) (.+)$", re.DOTALL)
_PLACEHOLDERS = ("[Photo]", "[Video]")   # what the integrations store when a description was not possible
_TEXT_LABEL = "\nText message: "

CLASS_DESCRIPTION = "description"   # the full description — the dim grey


def markup(content: str) -> str:
    """The pane's HTML for a system row."""
    m = _MESSAGE_RE.match(content)
    if not m:
        return html.escape(content, quote=False)
    channel, direction, sender, recipient, body = m.groups()
    parties = f"{sender} → {recipient}" if recipient else sender
    return f"<b>{html.escape(parties, quote=False)}</b>{_body(body)}"


def _body(body: str) -> str:
    media, _, text = body.partition(_TEXT_LABEL)
    m = _DESCRIPTION_RE.match(media)
    if m:
        short, description = m.groups()
    elif media in _PLACEHOLDERS:
        short, description = media, ""
    else:
        return " " + html.escape(body, quote=False)
    lines = [(" " + html.escape(text, quote=False)) if text else "", "\n" + html.escape(short, quote=False)]
    if description:
        lines.append(f'\n<span class="{CLASS_DESCRIPTION}">{html.escape(description, quote=False)}</span>')
    return "".join(lines)
