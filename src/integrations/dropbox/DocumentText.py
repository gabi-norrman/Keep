"""The text of a document and its pages: bytes and a name in, text out; then the paging and the find.

Pure functions, no I/O. `extract` reads what it has a reader for — plain text of any kind, a
PDF through pdfplumber with a `[p. N]` marker before each PDF page, a .docx or .pptx through
the stdlib zip and XML readers (`[slide N]` markers) — and raises `Unreadable` for the rest
(images, spreadsheets, archives, a binary it cannot place). `pages` cuts a text into pages of
at most PAGE_CHARS at paragraph boundaries, so a page and its header stay under the size the
history stores a command result at. `find` is the content search that works on any plan:
the lines of the text that carry every given word, each with its page and its PDF page."""
import io
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

# Under MessageHistory.COMMAND_RESULT_CHARS (6,000) with the header of a page over it.
PAGE_CHARS = 5000
FIND_LIMIT = 20
SNIPPET_CHARS = 200
TEXT_EXTENSIONS = {"txt", "md", "markdown", "csv", "tsv", "json", "yaml", "yml", "xml", "html", "htm", "log", "ini",
                   "toml", "cfg", "conf", "rtf", "tex", "py", "js", "ts", "sh", "sql", "java", "c", "h", "cpp", "rs",
                   "go", "rb", "php", "css", "srt", "vtt", "ics", "eml", "paper"}
BINARY_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "heic", "heif", "webp", "tif", "tiff", "bmp", "svg", "mp3", "m4a",
                     "wav", "aac", "flac", "mp4", "mov", "m4v", "avi", "mkv", "zip", "gz", "tgz", "rar", "7z", "dmg",
                     "pkg", "exe", "app", "xls", "xlsx", "numbers", "key", "pages", "doc", "ppt", "psd", "ai", "sketch"}
MARKER_RE = re.compile(r"^\[(?:p\.|slide) (\d+)\]$")
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DRAWING_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


class Unreadable(Exception):
    """The file has no text to give: its kind, or what went wrong, in a few words."""


@dataclass(frozen=True)
class Hit:
    page: int                   # the page of `pages()` the line is on
    marker: str                 # the PDF page or slide the line is under, "" for plain text
    line: str


def extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def extract(name: str, data: bytes) -> str:
    """The text of the file `name` holds in `data`; raises Unreadable."""
    ext = extension(name)
    if ext == "pdf":
        return pdf_text(data)
    if ext == "docx":
        return docx_text(data)
    if ext == "pptx":
        return pptx_text(data)
    if ext in BINARY_EXTENSIONS:
        raise Unreadable(f"a .{ext} file has no text to read")
    if ext in TEXT_EXTENSIONS or looks_like_text(data):
        return decode(data)
    raise Unreadable("the file is binary, not text" + (f" (.{ext})" if ext else ""))


def looks_like_text(data: bytes) -> bool:
    head = data[:8192]
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def pdf_text(data: bytes) -> str:
    import pdfplumber
    parts = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for number, page in enumerate(pdf.pages, 1):
                text = (page.extract_text() or "").strip()
                parts.append(f"[p. {number}]\n{text}" if text else f"[p. {number}]")
    except Exception as e:
        raise Unreadable(f"the PDF could not be read ({type(e).__name__}: {e})") from e
    if not any("\n" in part for part in parts):
        raise Unreadable(f"the PDF has {len(parts)} page(s) but no text layer (a scan — no OCR here)")
    return "\n\n".join(parts)


def _zip_member(data: bytes, member: str) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return archive.read(member)
    except (zipfile.BadZipFile, KeyError) as e:
        raise Unreadable(f"not a document archive ({type(e).__name__})") from e


def docx_text(data: bytes) -> str:
    """One line per paragraph, a table's rows one line each with tabs between the cells."""
    root = ElementTree.fromstring(_zip_member(data, "word/document.xml"))
    body = root.find(f"{WORD_NS}body")
    if body is None:
        raise Unreadable("the document has no body")
    lines = []
    for child in body:
        if child.tag == f"{WORD_NS}p":
            lines.append(_paragraph_text(child, WORD_NS))
        elif child.tag == f"{WORD_NS}tbl":
            for row in child.iter(f"{WORD_NS}tr"):
                cells = [" ".join(_paragraph_text(p, WORD_NS) for p in cell.iter(f"{WORD_NS}p")).strip()
                         for cell in row.findall(f"{WORD_NS}tc")]
                lines.append("\t".join(cells))
    return _tidy("\n".join(lines))


def pptx_text(data: bytes) -> str:
    """The slides in order, each under a `[slide N]` marker."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = sorted((n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                           key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
            slides = [(int(re.search(r"(\d+)\.xml$", n).group(1)), archive.read(n)) for n in names]
    except zipfile.BadZipFile as e:
        raise Unreadable("not a presentation archive") from e
    if not slides:
        raise Unreadable("the presentation has no slides")
    parts = []
    for number, xml in slides:
        root = ElementTree.fromstring(xml)
        lines = [_paragraph_text(p, DRAWING_NS) for p in root.iter(f"{DRAWING_NS}p")]
        text = "\n".join(line for line in lines if line.strip())
        parts.append(f"[slide {number}]\n{text}" if text else f"[slide {number}]")
    return "\n\n".join(parts)


def _paragraph_text(paragraph, ns: str) -> str:
    return "".join(t.text or "" for t in paragraph.iter(f"{ns}t")).strip()


def _tidy(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# --- paging ----------------------------------------------------------------------------------

def pages(text: str, limit: int = PAGE_CHARS) -> list[str]:
    """The text cut into pages of at most `limit` characters, at a blank line when one is in
    reach, else a line end, else a space, else hard. An empty text is one empty page."""
    text = text.strip()
    if not text:
        return [""]
    out = []
    while len(text) > limit:
        window = text[:limit]
        cut = next((at for at in (window.rfind("\n\n", limit // 4), window.rfind("\n", limit // 2),
                                  window.rfind(" ", limit * 3 // 4)) if at > 0), limit)
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    out.append(text)
    return out


def find(text: str, words: list[str], limit: int = FIND_LIMIT) -> list[Hit]:
    """The lines carrying every word (case aside), each with its page of `pages()` and the
    `[p. N]` / `[slide N]` marker it stands under; at most `limit` hits."""
    wanted = [w.lower() for w in words if w.strip()]
    if not wanted:
        return []
    hits: list[Hit] = []
    marker = ""
    for number, page in enumerate(pages(text), 1):
        for line in page.splitlines():
            stripped = line.strip()
            if match := MARKER_RE.match(stripped):
                marker = stripped
                continue
            lowered = stripped.lower()
            if stripped and all(w in lowered for w in wanted):
                hits.append(Hit(number, marker, snippet(stripped, wanted[0])))
                if len(hits) >= limit:
                    return hits
    return hits


def snippet(line: str, word: str, limit: int = SNIPPET_CHARS) -> str:
    """The line, or the part of it around the first `word` when it is longer than `limit`."""
    if len(line) <= limit:
        return line
    at = line.lower().find(word)
    start = max(0, min(at - limit // 3, len(line) - limit))
    piece = line[start:start + limit]
    return ("…" if start > 0 else "") + piece + ("…" if start + limit < len(line) else "")
