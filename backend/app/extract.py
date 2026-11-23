"""Turns uploaded files into indexable text.

Documents (one entry each): .pdf .docx .txt .md .html
Tables (one entry per row, ideal for bulk data): .csv .tsv .xlsx .json .jsonl/.ndjson
"""
import csv
import io
import json
import re
from typing import Iterator

DOCUMENT_TYPES = (".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm")
TABLE_TYPES = (".csv", ".tsv", ".xlsx", ".json", ".jsonl", ".ndjson")
SUPPORTED = DOCUMENT_TYPES + TABLE_TYPES
TITLE_GUESSES = ("title", "name", "full_name", "fullname", "candidate_name", "subject", "heading", "company", "email")


def kind_of(filename: str) -> str | None:
    n = filename.lower()
    if n.endswith(TABLE_TYPES):
        return "table"
    if n.endswith(DOCUMENT_TYPES):
        return "document"
    return None


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("cp1252", errors="replace")


def document_text(filename: str, data: bytes) -> str:
    n = filename.lower()
    if n.endswith(".pdf"):
        from pypdf import PdfReader

        return "\n\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages)
    if n.endswith(".docx"):
        import docx

        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n\n".join(parts)
    text = _decode(data)
    if n.endswith((".html", ".htm")):
        text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
    return text


def table_rows(filename: str, data: bytes) -> Iterator[dict]:
    n = filename.lower()
    if n.endswith(".xlsx"):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        for ws in wb.worksheets:
            header = None
            for row in ws.iter_rows(values_only=True):
                if header is None:
                    header = [str(h).strip() if h is not None else f"col{i + 1}" for i, h in enumerate(row)]
                    continue
                rec = {header[i] if i < len(header) else f"col{i + 1}": v for i, v in enumerate(row) if v not in (None, "")}
                if rec:
                    if len(wb.worksheets) > 1:
                        rec["sheet"] = ws.title
                    yield rec
        return
    text = _decode(data)
    if n.endswith((".jsonl", ".ndjson")):
        for line in text.splitlines():
            line = line.strip()
            if line:
                obj = json.loads(line)
                yield obj if isinstance(obj, dict) else {"value": obj}
        return
    if n.endswith(".json"):
        obj = json.loads(text)
        if isinstance(obj, dict):
            # {"data": [...]} style wrappers
            lists = [v for v in obj.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
            obj = lists[0] if len(lists) == 1 else [obj]
        for item in obj:
            yield item if isinstance(item, dict) else {"value": item}
        return
    sample = text[:5000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if n.endswith(".tsv") else csv.excel
    for row in csv.DictReader(io.StringIO(text), dialect=dialect):
        rec = {(k or "").strip(): v for k, v in row.items() if k and v not in (None, "")}
        if rec:
            yield rec


def row_title(row: dict, title_field: str | None, fallback: str) -> str:
    if title_field and row.get(title_field) not in (None, ""):
        return str(row[title_field])[:200]
    lower = {k.lower(): k for k in row}
    for g in TITLE_GUESSES:
        if g in lower and row[lower[g]] not in (None, ""):
            return str(row[lower[g]])[:200]
    return fallback
