"""The text shape of a browse snapshot, shared by the renderer that writes it and the history
compressor that collapses it once a later snapshot has replaced it.

WebAccess assembles a snapshot as a header (NOTE lines, `URL:`, `Title:`, `Page:`, `Alerts:`,
`Text:`), a blank line, ELEMENTS_MARKER and then the element list: one `[index] …` line per
main-content element, GROUP_SEPARATOR and one `region: N items — …` line per collapsed
header/nav/footer group, and optionally the IFRAMES_NOTE paragraph with one `  - src` line
per unreachable frame. The compressor lives in `assistant.prompt` and must not import the
browser stack, so the constants live here instead of in WebAccess."""
import re

ELEMENTS_MARKER = "Interactive elements (indices reset every snapshot):"
GROUP_SEPARATOR = "--- header/nav/footer (collapsed) ---"
NO_ELEMENTS = "(no interactive elements found on this page)"
IFRAMES_NOTE = "Cross-origin iframes on this page"
TEXT_PREFIX = "Text: "
URL_PREFIX = "URL: "

ELEMENT_LINE = re.compile(r"^\[\d+\] ")
GROUP_LINE = re.compile(r"^\w+: \d+ items — ")
IFRAME_LINE = re.compile(r"^  - \S")
ELEMENT_INDEX = re.compile(r"\[(\d+)\]")


def is_element_list_line(line: str) -> bool:
    """Whether `line` belongs to a snapshot's element list (the part below ELEMENTS_MARKER)."""
    return (line == GROUP_SEPARATOR or line == NO_ELEMENTS or line.startswith(IFRAMES_NOTE)
            or bool(ELEMENT_LINE.match(line)) or bool(GROUP_LINE.match(line))
            or bool(IFRAME_LINE.match(line)))
