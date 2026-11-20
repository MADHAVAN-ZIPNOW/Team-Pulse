"""Turns Azure DevOps attachments into something Claude can read: images stay images, documents become text."""
from __future__ import annotations

import html
import io
import re
import zipfile

MAX_PDF_PAGES = 60


def _xml_text(xml: str, paragraph_end: str) -> str:
    xml = re.sub(paragraph_end, "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(xml)).strip()


def extract_document_text(data: bytes) -> tuple[str, str] | None:
    """Returns (kind, text) for docx/pptx/xlsx/pdf/plain text, or None if the format is unknown."""
    if data.startswith(b"%PDF"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "") for p in reader.pages[:MAX_PDF_PAGES]]
        note = f"\n[only first {MAX_PDF_PAGES} of {len(reader.pages)} pages]" if len(reader.pages) > MAX_PDF_PAGES else ""
        text = "\n\n".join(f"--- Page {i + 1} ---\n{t.strip()}" for i, t in enumerate(pages)) + note
        return "pdf", text
    if data.startswith(b"PK"):
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return None
        names = z.namelist()
        if "word/document.xml" in names:
            return "docx", _xml_text(z.read("word/document.xml").decode("utf-8", "replace"), r"</w:p>|<w:br\s*/>")
        slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
        if slides:
            parts = []
            for n in slides:
                xml = z.read(n).decode("utf-8", "replace")
                texts = [html.unescape(t) for t in re.findall(r"<a:t>([^<]*)</a:t>", xml)]
                number = re.findall(r"\d+", n)[-1]
                parts.append(f"--- Slide {number} ---\n" + "\n".join(texts))
            return "pptx", "\n\n".join(parts)
        if "xl/sharedStrings.xml" in names:
            xml = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            return "xlsx", "\n".join(html.unescape(t) for t in re.findall(r"<t[^>]*>([^<]*)</t>", xml))
        return None
    if b"\x00" not in data[:8000]:
        try:
            return "text", data.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None
