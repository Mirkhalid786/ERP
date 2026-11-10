"""
Reading text from supplier bills, behind a provider-independent interface.

  extract_document(data, content_type) -> Extracted(lines, method, pages, warnings, confidence)

Providers:
  * "local" (default): text-based PDFs are read from their text layer (pypdfium2). Scanned PDFs and photos are read
    with RapidOCR (ONNX models bundled in the package; runs on this server, nothing is sent anywhere).
  * "none": no automatic reading; every bill is entered by hand on the review screen.
Set BILL_EXTRACTOR=local|none. To add another engine (e.g. a cloud document-AI service), implement
DocumentExtractor and register it in EXTRACTORS; it must be configured explicitly because bills would leave the server.

The output is untrusted text. It is only parsed for values; it is never executed or followed as instructions.
"""
import io
import os
import re
import statistics
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Protocol

MAX_PAGES = 10
OCR_DPI = 200
MIN_TEXT_CHARS = 40          # a PDF page with less real text than this is treated as scanned


@dataclass
class Extracted:
    lines: List[str]
    method: str
    pages: int
    warnings: List[str] = field(default_factory=list)
    confidence: Optional[float] = None       # mean OCR confidence (0-1); None for a text layer


class DocumentExtractor(Protocol):
    name: str

    def extract(self, data: bytes, content_type: str) -> Extracted: ...


# ── OCR engine (loaded once, used by one thread at a time) ─────────────────────
_engine = None
_engine_lock = threading.Lock()


def _ocr_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _engine = RapidOCR()
    return _engine


def _split_words(gray, img, box, engine) -> tuple:
    """Recognise one detected text box, splitting it at blank vertical gaps wider than a letter gap so that
    table columns ("10  1  33.60  24.15") are not merged into one token."""
    import cv2
    import numpy as np
    pts = np.array(box, dtype=np.float32)
    x0, y0 = [max(int(v), 0) for v in pts.min(axis=0)]
    x1, y1 = [int(v) for v in pts.max(axis=0)]
    if x1 - x0 < 4 or y1 - y0 < 4:
        return "", 0.0, y0, y1, x0
    crop = gray[y0:y1, x0:x1]
    h = y1 - y0
    _, bw = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ink = (bw > 0).sum(axis=0) > 0
    gap_min = max(int(h * 0.38), 4)
    segments, start, blank = [], None, 0
    for x, on in enumerate(ink):
        if on:
            if start is None:
                start = x
            blank = 0
        else:
            blank += 1
            if start is not None and blank == gap_min:
                segments.append((start, x - gap_min + 1))
                start = None
    if start is not None:
        segments.append((start, len(ink)))
    words, scores = [], []
    for a, z in segments:
        piece = img[y0:y1, x0 + max(a - 3, 0): x0 + min(z + 3, x1 - x0)]
        res, _ = engine(piece, use_det=False, use_cls=False)
        if res and res[0][0].strip():
            words.append(res[0][0].strip())
            scores.append(float(res[0][1]))
    text = " ".join(words)
    text = re.sub(r"(\d) ([.,/])(\d)", r"\1\2\3", text)          # "871 .43" -> "871.43"
    text = re.sub(r"(\d)([.,/]) (\d)", r"\1\2\3", text)
    text = re.sub(r"\b(\d) (\d/\d{2,4})\b", r"\1\2", text)       # "1 1/27" -> "11/27"
    return text, (sum(scores) / len(scores) if scores else 0.0), y0, y1, x0


def ocr_image(img) -> tuple:
    """OCR one page image (BGR ndarray) into text lines ordered top-to-bottom, left-to-right."""
    import cv2
    with _engine_lock:
        engine = _ocr_engine()
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        boxes, _ = engine(img, use_cls=False, use_rec=False)
        pieces = [_split_words(gray, img, b, engine) for b in (boxes or [])]
    pieces = [p for p in pieces if p[0]]
    if not pieces:
        return [], 0.0
    # Group boxes into lines by vertical overlap, then read each line left to right
    pieces.sort(key=lambda p: (p[2] + p[3]) / 2)
    lines, current, cur_mid, cur_h = [], [], None, None
    for text, conf, y0, y1, x0 in pieces:
        mid, h = (y0 + y1) / 2, y1 - y0
        if current and abs(mid - cur_mid) <= max(cur_h, h) * 0.5:
            current.append((x0, text, conf))
        else:
            if current:
                lines.append(current)
            current, cur_mid, cur_h = [(x0, text, conf)], mid, h
    if current:
        lines.append(current)
    out = ["  ".join(t for _, t, _ in sorted(line)) for line in lines]
    confidence = statistics.mean(c for line in lines for _, _, c in line)
    return out, confidence


def _decode_image(data: bytes):
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("The image could not be decoded")
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest > 3000:                       # phone photos: keep OCR time bounded
        scale = 3000 / longest
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    elif longest < 1200:                     # small images: enlarge so small print is legible
        scale = 1600 / longest
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    return img


class LocalExtractor:
    name = "local"

    def extract(self, data: bytes, content_type: str) -> Extracted:
        if content_type == "application/pdf":
            return self._pdf(data)
        lines, conf = ocr_image(_decode_image(data))
        warnings = [] if lines else ["No text was found in the image"]
        return Extracted(lines, "ocr-local", 1, warnings, conf if lines else None)

    def _pdf(self, data: bytes) -> Extracted:
        import cv2
        import numpy as np
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(data)
        try:
            pages = len(doc)
            warnings = []
            if pages > MAX_PAGES:
                warnings.append(f"Only the first {MAX_PAGES} of {pages} pages were read")
            lines, used_ocr, confs = [], False, []
            for i in range(min(pages, MAX_PAGES)):
                page = doc[i]
                text = page.get_textpage().get_text_bounded() or ""
                if len(re.sub(r"\s", "", text)) >= MIN_TEXT_CHARS:
                    lines += [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
                else:
                    used_ocr = True
                    pil = page.render(scale=OCR_DPI / 72).to_pil().convert("RGB")
                    img = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
                    page_lines, conf = ocr_image(img)
                    lines += page_lines
                    if page_lines:
                        confs.append(conf)
            if not lines:
                warnings.append("No text was found in the document")
            return Extracted(lines, "ocr-local" if used_ocr else "pdf-text", pages, warnings,
                             statistics.mean(confs) if confs else None)
        finally:
            doc.close()


class ManualOnly:
    name = "none"

    def extract(self, data: bytes, content_type: str) -> Extracted:
        return Extracted([], "none", 0, ["Automatic reading is switched off (BILL_EXTRACTOR=none); enter the bill by hand"])


EXTRACTORS = {"local": LocalExtractor, "none": ManualOnly}


def get_extractor() -> DocumentExtractor:
    name = os.environ.get("BILL_EXTRACTOR", "local").strip().lower() or "local"
    return EXTRACTORS.get(name, LocalExtractor)()


def extract_document(data: bytes, content_type: str) -> Extracted:
    return get_extractor().extract(data, content_type)
