"""Safe text extraction from uploaded CVs / job postings (PDF, DOCX, TXT/MD, HTML)."""
from __future__ import annotations

import base64
import binascii
import html as htmllib
import io
import re
import zipfile

from src.service.errors import EngineError

MAX_DOC_BYTES = 12 * 1024 * 1024
MAX_TEXT_CHARS = 60_000
_MAX_UNZIPPED = 25 * 1024 * 1024


def strip_html(raw: str) -> str:
    if not raw:
        return ""
    t = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|aside)\b.*?</\1>", " ", raw)
    t = re.sub(r"(?i)<li[^>]*>", "\n- ", t)
    t = re.sub(r"(?i)</(p|div|h[1-6]|section|article|tr|li|ul|ol)>|<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = htmllib.unescape(t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\s*\n\s*", "\n", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > _MAX_UNZIPPED:
                raise EngineError("The DOCX file is too large to process.", 413)
            names = [n for n in z.namelist() if n in ("word/document.xml",) or re.match(r"word/(header|footer)\d*\.xml", n)]
            parts = []
            for n in names:
                xml = z.read(n).decode("utf8", "ignore")
                xml = re.sub(r"</w:p>", "\n", xml)
                xml = re.sub(r"<w:tab/>", " ", xml)
                xml = re.sub(r"<w:br\s*/>", "\n", xml)
                parts.append(htmllib.unescape(re.sub(r"<[^>]+>", "", xml)))
            return "\n".join(parts)
    except zipfile.BadZipFile as exc:
        raise EngineError("The file is not a valid DOCX document.", 422) from exc


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise EngineError("The PDF is password-protected. Remove the password and try again.", 422)
        return "\n".join((page.extract_text() or "") for page in reader.pages[:40])
    except EngineError:
        raise
    except Exception as exc:  # pypdf raises many types on malformed files
        raise EngineError("Could not read the PDF. Try a text-based PDF or paste the text instead.", 422) from exc


def document_text(doc: dict | None) -> str:
    """Return plain text for an uploaded document payload ({name, mimeType, base64Data, textContent})."""
    if not doc:
        return ""
    text = (doc.get("textContent") or "").strip()
    if text:
        return text[:MAX_TEXT_CHARS]
    b64 = doc.get("base64Data") or ""
    if not b64:
        return ""
    if len(b64) > MAX_DOC_BYTES * 4 // 3 + 16:
        raise EngineError("The uploaded file is too large (limit 12 MB).", 413)
    try:
        data = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise EngineError("The uploaded file could not be decoded.", 400) from exc
    name = (doc.get("name") or "").lower()
    mime = (doc.get("mimeType") or "").lower()
    if name.endswith(".pdf") or mime == "application/pdf" or data[:4] == b"%PDF":
        out = _pdf_text(data)
    elif name.endswith(".docx") or "wordprocessingml" in mime or data[:2] == b"PK":
        out = _docx_text(data)
    elif name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")) or mime.startswith("image/"):
        raise EngineError(
            "Image files cannot be read without OCR. Upload a PDF, DOCX or TXT file, or paste the text.", 422)
    elif name.endswith(".doc"):
        raise EngineError("Legacy .doc files are not supported. Save the file as DOCX or PDF.", 422)
    else:
        out = data.decode("utf8", "ignore")
        if "<html" in out[:500].lower():
            out = strip_html(out)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out[:MAX_TEXT_CHARS]


_EXPIRED = [
    r"no longer accepting (?:applications|candidates)",
    r"(?:this\s+)?job(?:\s+posting|\s+listing)?\s+(?:has\s+|is\s+)?expired",
    r"posting\s+(?:has\s+)?expired",
    r"(?:this\s+)?(?:position|role|job|opening|requisition)\s+(?:has\s+been\s+|is\s+now\s+|is\s+)?(?:filled|closed|archived|cancelled|canceled|removed|withdrawn)\b",
    r"sorry,\s*(?:this\s+)?(?:job|position|posting|role)\s+(?:is\s+)?(?:closed|no longer available|has expired)",
    r"applications?\s+(?:for\s+this\s+(?:role|position|job)\s+)?(?:are\s+|have\s+)?(?:now\s+)?closed",
]
_NOT_FOUND = [
    r"(?:job(?:\s+posting|\s+listing)?|position|requisition)\s+(?:was\s+)?not\s+found",
    r"(?:job|posting|position)\s+(?:could\s+not|cannot|can't)\s+be\s+found",
    r"page\s+not\s+found",
]


def detect_unavailable(text: str) -> tuple[str, str] | None:
    """Return (status, reason) when text indicates an expired / missing posting."""
    t = text or ""
    for pat in _EXPIRED:
        if re.search(pat, t, re.I):
            return "expired", "This job posting has expired or is no longer accepting applications."
    if len(t.strip()) < 1600:
        for pat in _NOT_FOUND:
            if re.search(pat, t, re.I):
                return "not_found", "This job posting could not be found or has been removed."
        if re.search(r"\b(?:error\s*404|404\s*[-:|]?\s*not\s+found)\b", t, re.I):
            return "not_found", "This job posting returned a 404 Not Found page."
    return None
