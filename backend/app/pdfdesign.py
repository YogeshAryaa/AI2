"""PDF designs for documents made in the chat. Same content, different looks.

Templates (each takes a main colour and an optional second colour for headings):
  classic  - colour strip on top, coloured title
  banner   - big coloured header block with the title in white
  minimal  - black and white, thin coloured lines only
  sidebar  - coloured stripe down the left of every page
  boxed    - headings inside soft coloured boxes
  elegant  - centred title, centred headings in capitals, fine rules
"""
import re
from datetime import datetime

TEMPLATES = {
    "classic": ("Classic", r"classic|normal|default|standard"),
    "banner": ("Banner", r"banner|header|bold|modern|heading\s*block"),
    "minimal": ("Minimal", r"minimal|simple|plain|clean|saada|sada|सादा"),
    "sidebar": ("Sidebar", r"sidebar|side\s*bar|side\s*wala|stripe|patti"),
    "boxed": ("Boxed", r"boxed|box|boxes|box\s*wala|card|cards"),
    "elegant": ("Elegant", r"elegant|formal|classy|royal|premium|stylish"),
}
ORDER = list(TEMPLATES)
_TPL_RE = {k: re.compile(rf"\b({w})\b", re.I) for k, (_, w) in TEMPLATES.items()}


def find_template(text: str) -> str | None:
    hits = [(m.start(), k) for k, rx in _TPL_RE.items() if (m := rx.search(text))]
    return min(hits)[1] if hits else None


def next_templates(current: str | None, n: int) -> list[str]:
    """n templates that differ from `current`, in a fixed rotation."""
    start = (ORDER.index(current) + 1) if current in ORDER else 0
    out = [ORDER[(start + i) % len(ORDER)] for i in range(len(ORDER))]
    out = [t for t in out if t != current] or out
    return out[:max(1, n)]


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _tint(rgb, amount: float) -> tuple[int, int, int]:
    return tuple(round(c * amount + 255 * (1 - amount)) for c in rgb)


def _dark(rgb, k: float = 0.75) -> tuple[int, int, int]:
    """A deeper shade for text. Very light colours (white, yellow) become readable."""
    lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255
    if lum > 0.8:
        return (40, 44, 52)
    return tuple(round(c * (k if lum < 0.55 else 0.55)) for c in rgb)


def _on(rgb) -> tuple[int, int, int]:
    lum = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255
    return (25, 25, 25) if lum > 0.6 else (255, 255, 255)


def build(title: str, body: str, citations: list[dict] | None = None, subtitle: str = "",
          accent: str = "#2563eb", secondary: str | None = None, template: str = "classic") -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos
    from fpdf.fonts import FontFace

    from .media import _clean_inline, _pdf_fonts, _table_rows

    tpl = template if template in TEMPLATES else "classic"
    acc = _rgb(accent)
    sec = _rgb(secondary) if secondary else acc
    head_c = _dark(sec)
    title_c = _dark(acc)
    ink = (30, 30, 30)
    date_line = subtitle or datetime.now().strftime("%d %b %Y")

    class Doc(FPDF):
        def header(self):
            if tpl == "sidebar":
                self.set_fill_color(*acc)
                self.rect(0, 0, 7, self.h, style="F")
                if secondary:
                    self.set_fill_color(*sec)
                    self.rect(7, 0, 1.5, self.h, style="F")
            elif tpl == "classic" and self.page_no() > 1:
                self.set_fill_color(*acc)
                self.rect(0, 0, self.w, 2, style="F")

        def footer(self):
            self.set_y(-12)
            self.set_font("Noto", "", 8)
            self.set_text_color(140)
            if tpl in ("banner", "elegant", "boxed"):
                self.cell(0, 6, f"{title[:70]}   ·   {self.page_no()} / {{nb}}", align="C")
            else:
                self.cell(0, 6, f"{self.page_no()} / {{nb}}", align="C")

    pdf = Doc(format="A4")
    pdf.set_title(title[:200])
    pdf.set_creator("Ask my data")
    _pdf_fonts(pdf)
    left = 26 if tpl == "sidebar" else 18
    pdf.set_margins(left, 18, 18)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()

    def para(text, size=11, style="", indent=0.0, h=None, color=ink, align="L", width=None):
        pdf.set_font("Noto", style, size)
        pdf.set_text_color(*color)
        pdf.set_x(pdf.l_margin + indent)
        pdf.multi_cell(width or (pdf.epw - indent), h or size * 0.55, _clean_inline(text), markdown=True,
                       align=align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # ---------------- title area
    if tpl == "classic":
        pdf.set_fill_color(*acc)
        pdf.rect(0, 0, pdf.w, 5, style="F")
        para(title, 20, "B", h=10, color=title_c)
        para(date_line, 9, color=(120, 120, 120))
        pdf.ln(1.5)
        pdf.set_fill_color(*acc)
        pdf.rect(pdf.l_margin, pdf.get_y(), 28, 1.2, style="F")
        pdf.ln(6)
    elif tpl == "banner":
        pdf.set_font("Noto", "B", 22)
        lines = pdf.multi_cell(pdf.epw, 10.5, _clean_inline(title), dry_run=True, output="LINES")
        hh = 22 + len(lines) * 10.5
        try:
            from fpdf.pattern import LinearGradient

            with pdf.use_pattern(LinearGradient(0, 0, pdf.w, hh, [acc, sec if secondary else _dark(acc, 0.8)])):
                pdf.rect(0, 0, pdf.w, hh, style="F")
        except Exception:
            pdf.set_fill_color(*acc)
            pdf.rect(0, 0, pdf.w, hh, style="F")
        pdf.set_y(12)
        para(title, 22, "B", h=10.5, color=_on(acc))
        para(date_line, 9.5, color=_on(acc))
        pdf.set_y(hh + 9)
    elif tpl == "minimal":
        pdf.ln(4)
        para(title, 24, "B", h=11, color=(20, 20, 20))
        pdf.ln(1)
        para(date_line, 9, color=(130, 130, 130))
        pdf.ln(3)
        pdf.set_draw_color(*acc)
        pdf.set_line_width(0.4)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(7)
    elif tpl == "sidebar":
        para(date_line.upper(), 8.5, "B", color=_dark(acc))
        pdf.ln(1)
        para(title, 22, "B", h=10.5, color=(20, 20, 20))
        pdf.ln(7)
    elif tpl == "boxed":
        pdf.set_font("Noto", "B", 19)
        lines = pdf.multi_cell(pdf.epw - 12, 9.5, _clean_inline(title), dry_run=True, output="LINES")
        bh = 14 + len(lines) * 9.5
        y0 = pdf.get_y()
        pdf.set_fill_color(*_tint(acc, 0.10))
        pdf.rect(pdf.l_margin, y0, pdf.epw, bh, style="F", round_corners=True, corner_radius=3)
        pdf.set_fill_color(*acc)
        pdf.rect(pdf.l_margin, y0, 2.5, bh, style="F")
        pdf.set_y(y0 + 5)
        para(title, 19, "B", indent=8, h=9.5, color=title_c)
        para(date_line, 9, indent=8, color=(110, 110, 110))
        pdf.set_y(y0 + bh + 8)
    else:  # elegant
        pdf.ln(6)
        pdf.set_draw_color(*acc)
        pdf.set_line_width(0.3)
        cx = pdf.w / 2
        pdf.line(cx - 20, pdf.get_y(), cx + 20, pdf.get_y())
        pdf.ln(5)
        para(title, 22, "B", h=10.5, color=title_c, align="C")
        pdf.ln(1)
        pdf.set_char_spacing(1.5)
        para(date_line.upper(), 8.5, color=(130, 130, 130), align="C")
        pdf.set_char_spacing(0)
        pdf.ln(3)
        pdf.line(cx - 20, pdf.get_y(), cx + 20, pdf.get_y())
        pdf.ln(9)

    # ---------------- blocks
    def heading(text, level):
        size = {1: 16, 2: 14, 3: 12.5}.get(level, 11.5)
        text = text.replace("**", "")
        pdf.ln(2.5 if level > 2 else 4)
        if level > 2:
            para(text, size, "B", color=(25, 25, 25))
            pdf.ln(1)
            return
        if pdf.will_page_break(22):
            pdf.add_page()
        if tpl == "boxed":
            pdf.set_font("Noto", "B", size - 1)
            y0 = pdf.get_y()
            h = size * 0.55 + 5
            pdf.set_fill_color(*_tint(sec, 0.13))
            pdf.rect(pdf.l_margin, y0, pdf.epw, h, style="F", round_corners=True, corner_radius=2)
            pdf.set_y(y0 + 2.5)
            para(text, size - 1, "B", indent=4, color=head_c)
            pdf.set_y(y0 + h + 3)
        elif tpl == "minimal":
            para(text, size - 1, "B", color=(20, 20, 20))
            pdf.set_draw_color(*_tint(acc, 0.5))
            pdf.set_line_width(0.25)
            pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
            pdf.ln(4)
        elif tpl == "elegant":
            pdf.set_char_spacing(1.2)
            para(text.upper(), size - 2.5, "B", color=head_c, align="C")
            pdf.set_char_spacing(0)
            pdf.set_fill_color(*acc)
            pdf.rect(pdf.w / 2 - 6, pdf.get_y() + 1.2, 12, 0.8, style="F")
            pdf.ln(5.5)
        elif tpl == "sidebar":
            y0 = pdf.get_y()
            para(text, size, "B", color=head_c)
            pdf.set_fill_color(*sec)
            pdf.ellipse(pdf.l_margin - 5.5, y0 + size * 0.2, 2.6, 2.6, style="F")
            pdf.ln(2)
        else:  # classic, banner
            para(text, size, "B", color=head_c)
            if tpl == "banner":
                pdf.set_fill_color(*_tint(sec, 0.6))
                pdf.rect(pdf.l_margin, pdf.get_y() + 0.6, 16, 0.9, style="F")
                pdf.ln(3)
            else:
                pdf.ln(1.5)

    def bullet(text, depth):
        ind = 4 + depth * 5
        y0 = pdf.get_y()
        pdf.set_fill_color(*acc)
        if tpl == "minimal":
            pdf.rect(pdf.l_margin + ind, y0 + 2.8, 2.2, 0.5, style="F")
        elif tpl in ("boxed", "sidebar"):
            pdf.rect(pdf.l_margin + ind, y0 + 2.1, 1.7, 1.7, style="F")
        else:
            pdf.ellipse(pdf.l_margin + ind, y0 + 2.2, 1.6, 1.6, style="F")
        pdf.set_y(y0)
        para(text, indent=ind + 4.5)
        pdf.ln(0.8)

    def table(rows):
        pdf.set_font("Noto", "", 9.5)
        pdf.set_text_color(30)
        if tpl in ("banner", "boxed"):
            head = FontFace(emphasis="BOLD", fill_color=sec if secondary else acc, color=_on(sec if secondary else acc))
        elif tpl == "minimal":
            head = FontFace(emphasis="BOLD", color=(20, 20, 20))
        else:
            head = FontFace(emphasis="BOLD", fill_color=_tint(sec, 0.14), color=head_c)
        pdf.set_fill_color(255, 255, 255)  # do not inherit a bullet colour
        pdf.set_draw_color(*_tint(acc, 0.35))
        layout = {"minimal": "HORIZONTAL_LINES", "boxed": "ALL", "elegant": "SINGLE_TOP_LINE"}.get(tpl, "SINGLE_TOP_LINE")
        with pdf.table(first_row_as_headings=len(rows) > 1, line_height=5.5, padding=1.6, headings_style=head,
                       borders_layout=layout, cell_fill_color=None if tpl == "minimal" else _tint(acc, 0.05),
                       cell_fill_mode="NONE" if tpl == "minimal" else "ROWS") as t:
            for r in rows:
                row = t.row()
                for c in r:
                    row.cell(c)
        pdf.ln(3)

    lines = body.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        s = ln.strip()
        if not s:
            pdf.ln(2.5)
            i += 1
            continue
        if s.startswith("|") and s.count("|") >= 2:
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            rows = _table_rows(block)
            if rows:
                table(rows)
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m:
            heading(m.group(2), len(m.group(1)))
        elif re.match(r"^([-*•+])\s+", s):
            bullet(re.sub(r"^([-*•+])\s+", "", s), min((len(ln) - len(ln.lstrip())) // 2, 3))
        elif re.match(r"^\d+[.)]\s+", s):
            para(s, indent=4 + min((len(ln) - len(ln.lstrip())) // 2, 3) * 5)
            pdf.ln(0.8)
        elif re.fullmatch(r"[-*_]{3,}", s):
            pdf.ln(1)
            pdf.set_draw_color(*_tint(acc, 0.4))
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(3)
        elif s.startswith(">"):
            y0 = pdf.get_y()
            para(s.lstrip("> "), indent=6, color=(90, 90, 90))
            pdf.set_fill_color(*acc)
            pdf.rect(pdf.l_margin + 1.5, y0, 1, pdf.get_y() - y0, style="F")
        else:
            para(s)
            pdf.ln(1.2)
        i += 1

    if citations:
        heading("Sources", 3)
        for c in citations:
            para(f"[{c.get('index')}] {c.get('title') or c.get('doc_id')}"
                 + (f"  ({c['source']})" if c.get("source") else ""), 9, indent=2, color=(90, 90, 90))
    return bytes(pdf.output())
