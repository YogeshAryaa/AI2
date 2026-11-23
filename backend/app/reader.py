"""Reads any uploaded file into plain text, on this server, without any AI service.

* PDF with real text  -> text straight from the file (pypdf)
* scanned PDF         -> each page is rendered (pypdfium2) and read with OCR
* image (jpg/png/...) -> OCR
* .docx / .txt / .md / .html -> extract.document_text

OCR is EasyOCR (English + Hindi by default, OCR_LANGUAGES in .env). Its models (~100 MB) download once,
the first time an image is read; after that everything runs offline on the CPU.
"""
import io
import logging
import re
import threading
import warnings

from .config import cfg
from .extract import document_text

log = logging.getLogger("rag.reader")

IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif")
TEXT_TYPES = (".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm")
READABLE = IMAGE_TYPES + TEXT_TYPES
MAX_OCR_PAGES = 15
MAX_SIDE = 1600  # bigger images are scaled down first: nearly the same accuracy, much faster

_reader = None
_lock = threading.Lock()


class ReadError(Exception):
    pass


def _ocr_engine():
    global _reader
    if _reader is None:
        try:
            import easyocr
        except ImportError as e:
            raise ReadError("OCR is not installed. Run: pip install easyocr pypdfium2") from e
        langs = [x.strip() for x in cfg.ocr_languages.split(",") if x.strip()] or ["en"]
        log.info("Loading OCR models for %s (the first time this downloads ~100 MB)", langs)
        _reader = easyocr.Reader(langs, gpu=False, model_storage_directory=cfg.ocr_model_dir or None,
                                 verbose=False)
    return _reader


_DEVA_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def _fix_digits(line: str) -> str:
    """The Hindi model writes digits as ३ even inside English lines: keep those lines in 0-9."""
    latin = len(re.findall(r"[A-Za-z]", line))
    deva = len(re.findall(r"[ऀ-॥॰-ॿ]", line))
    return line.translate(_DEVA_DIGITS) if latin >= deva else line


def _lines(results) -> str:
    """EasyOCR gives word boxes; put boxes on the same row together, top to bottom, left to right."""
    boxes = []
    for box, text, conf in results:
        if not text.strip() or conf < 0.15:
            continue
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        boxes.append((min(ys), max(ys), min(xs), text.strip()))
    boxes.sort(key=lambda b: (b[0], b[2]))
    rows: list[list] = []
    for top, bottom, left, text in boxes:
        mid = (top + bottom) / 2
        for row in rows:
            r_top, r_bottom = row[0], row[1]
            if r_top <= mid <= r_bottom:
                row[2].append((left, text))
                row[0], row[1] = min(r_top, top), max(r_bottom, bottom)
                break
        else:
            rows.append([top, bottom, [(left, text)]])
    out, prev_bottom, prev_h = [], None, None
    for top, bottom, items in rows:
        h = bottom - top
        if prev_bottom is not None and top - prev_bottom > 1.2 * max(h, prev_h or h):
            out.append("")  # a bigger gap = a new paragraph / section
        out.append(_fix_digits(" ".join(t for _, t in sorted(items))))
        prev_bottom, prev_h = bottom, h
    return "\n".join(out)


def ocr_image(img) -> str:
    """img: a PIL image."""
    import numpy as np

    img = img.convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    with _lock:  # one OCR at a time: it already uses every CPU core
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # torch deprecation noise from EasyOCR
            res = _ocr_engine().readtext(np.array(img), detail=1, paragraph=False)
    return _lines(res)


def _ocr_pdf(data: bytes) -> tuple[str, int]:
    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        raise ReadError("Reading scanned PDFs needs: pip install pypdfium2") from e
    doc = pdfium.PdfDocument(data)
    n = len(doc)
    pages = []
    for i in range(min(n, MAX_OCR_PAGES)):
        pil = doc[i].render(scale=2).to_pil()
        pages.append(ocr_image(pil))
    if n > MAX_OCR_PAGES:
        pages.append(f"[Only the first {MAX_OCR_PAGES} of {n} pages were scanned]")
    return "\n\n".join(pages), n


def read(filename: str, data: bytes) -> dict:
    """{"text", "method": "text"|"ocr", "pages"}. Blocking: call it in a thread."""
    name = filename.lower()
    if name.endswith(IMAGE_TYPES):
        from PIL import Image, ImageOps

        try:
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        except Exception as e:
            raise ReadError(f"Not a readable image: {e}") from e
        return {"text": ocr_image(img), "method": "ocr", "pages": 1}
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            texts = [(pg.extract_text() or "") for pg in reader.pages]
        except Exception as e:
            raise ReadError(f"Not a readable PDF: {e}") from e
        n = len(texts)
        if n and sum(len(t.strip()) for t in texts) / n >= 40:
            return {"text": "\n\n".join(texts).strip(), "method": "text", "pages": n}
        text, n = _ocr_pdf(data)  # a scan: no real text inside
        return {"text": text, "method": "ocr", "pages": n}
    if name.endswith(TEXT_TYPES):
        return {"text": document_text(filename, data).strip(), "method": "text", "pages": 1}
    raise ReadError(f"Unsupported file type. Use: {', '.join(READABLE)}")
