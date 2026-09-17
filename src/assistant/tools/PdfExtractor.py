import io

import pdfplumber

from common.logging import info, note

MAX_PDF_SIZE = 50 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_PDF_TEXT_CHARS = 100_000


def extract_text(pdf_bytes: bytes) -> str:
    if len(pdf_bytes) > MAX_PDF_SIZE:
        note(f"PDF too large ({len(pdf_bytes)} bytes)")
        return "ERROR: PDF file is too large (over 50 MB)."

    try:
        pages_text = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            total_pages = len(pdf.pages)
            pages_to_read = min(total_pages, MAX_PDF_PAGES)
            if total_pages > MAX_PDF_PAGES:
                info(f"PDF has {total_pages} pages, reading first {MAX_PDF_PAGES}")

            for i, page in enumerate(pdf.pages[:pages_to_read]):
                text = page.extract_text()
                if text:
                    pages_text.append(f"--- Page {i + 1} ---\n{text}")

        if not pages_text:
            return "ERROR: PDF contains no extractable text (might be scanned/image-based)."

        result = '\n\n'.join(pages_text)

        truncated = False
        if len(result) > MAX_PDF_TEXT_CHARS:
            result = result[:MAX_PDF_TEXT_CHARS]
            truncated = True

        notes = []
        if total_pages > MAX_PDF_PAGES:
            notes.append(f"Only first {MAX_PDF_PAGES} of {total_pages} pages extracted.")
        if truncated:
            notes.append(f"Text truncated to {MAX_PDF_TEXT_CHARS} characters.")
        if notes:
            result += "\n\n[NOTE: " + " ".join(notes) + "]"

        info(f"Extracted {len(result)} chars from {pages_to_read}-page PDF")
        return result

    except Exception as e:
        note(f"Failed to extract text from PDF: {e}")
        return f"ERROR: Could not read PDF: {e}"
