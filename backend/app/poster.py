"""Poster maker with no AI: the user gives the words (and optionally a photo and a logo), a template lays
them out. Drawn as a PDF with fpdf2 (so Hindi letters join correctly), then rendered to PNG with pypdfium2.
Page units are points = output pixels, so a 1080 x 1080 poster renders to a 1080 x 1080 PNG.
"""
import io
import re

from .media import _clean_inline, _pdf_fonts

SIZES = {
    "square": (1080, 1080, "Square 1080×1080 (Instagram, LinkedIn post)"),
    "story": (1080, 1920, "Story 1080×1920 (WhatsApp / Instagram story)"),
    "a4": (1240, 1754, "A4 portrait (print)"),
    "landscape": (1920, 1080, "Landscape 1920×1080 (slides, screens)"),
}
THEMES = {
    "ocean": {"bg": ("#0f2a4a", "#1f6fb2"), "accent": "#ffd166", "text": "#ffffff", "muted": "#cfe3f7"},
    "midnight": {"bg": ("#111827", "#3b1d6e"), "accent": "#f472b6", "text": "#ffffff", "muted": "#d1d5db"},
    "forest": {"bg": ("#0b3d2e", "#1d7a55"), "accent": "#b8f36b", "text": "#ffffff", "muted": "#cdeedd"},
    "royal": {"bg": ("#3a0ca3", "#7209b7"), "accent": "#fca311", "text": "#ffffff", "muted": "#ead9ff"},
    "sunset": {"bg": ("#ff8a5c", "#ffd36e"), "accent": "#2a1205", "text": "#2a1205", "muted": "#4d2a14"},
    "clean": {"bg": ("#ffffff", "#e8eef7"), "accent": "#2563eb", "text": "#0f172a", "muted": "#475569"},
}


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _valid_hex(c: str | None) -> str | None:
    return c if c and re.fullmatch(r"#[0-9a-fA-F]{6}", c.strip()) else None


def _lum(rgb) -> float:
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255


def _cover(data: bytes, w: int, h: int) -> io.BytesIO:
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img = ImageOps.fit(img, (w, h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    buf.seek(0)
    return buf


def _logo(data: bytes, max_px: int) -> tuple[io.BytesIO, int, int]:
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    img = img.convert("RGBA")
    img.thumbnail((max_px * 3, max_px))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    return buf, img.width, img.height


def build(d: dict, background: bytes | None = None, logo: bytes | None = None) -> tuple[bytes, bytes]:
    """Returns (png, pdf)."""
    from fpdf import FPDF

    W, H, _ = SIZES.get(d.get("size") or "square", SIZES["square"])
    th = dict(THEMES.get(d.get("theme") or "ocean", THEMES["ocean"]))
    if _valid_hex(d.get("accent")):
        th["accent"] = d["accent"].strip()
    S = min(W, H)
    m = round(S * 0.075)
    text_c, muted_c, acc = _rgb(th["text"]), _rgb(th["muted"]), _rgb(th["accent"])
    if background:  # photo behind: always light text on a dark veil
        text_c, muted_c = (255, 255, 255), (225, 230, 238)

    pdf = FPDF(unit="pt", format=(W, H))
    pdf.set_creator("Ask my data")
    pdf.set_title((d.get("headline") or "Poster")[:120])
    _pdf_fonts(pdf)
    pdf.set_auto_page_break(False)
    pdf.set_margins(m, m, m)
    pdf.add_page()

    # ---------- background
    c1, c2 = _rgb(th["bg"][0]), _rgb(th["bg"][1])
    if background:
        pdf.image(_cover(background, W, H), x=0, y=0, w=W, h=H)
        with pdf.local_context(fill_opacity=0.62):
            pdf.set_fill_color(*[round(c * 0.6) for c in c1])
            pdf.rect(0, 0, W, H, style="F")
    else:
        try:
            from fpdf.pattern import LinearGradient

            with pdf.use_pattern(LinearGradient(0, 0, W, H, [c1, c2])):
                pdf.rect(0, 0, W, H, style="F")
        except Exception:  # older fpdf2: bands
            steps = 60
            for i in range(steps):
                t = i / (steps - 1)
                pdf.set_fill_color(*[round(a + (b - a) * t) for a, b in zip(c1, c2)])
                pdf.rect(0, H * i / steps, W, H / steps + 1, style="F")
        # soft decorative circles
        with pdf.local_context(fill_opacity=0.10):
            pdf.set_fill_color(*acc)
            r = S * 0.42
            pdf.ellipse(W - r * 1.25, -r * 0.55, r * 2, r * 2, style="F")
        with pdf.local_context(fill_opacity=0.07):
            pdf.set_fill_color(*text_c)
            r = S * 0.30
            pdf.ellipse(-r * 0.7, H - r * 1.55, r * 2, r * 2, style="F")

    def font(size, bold=False):
        pdf.set_font("Noto", "B" if bold else "", size)

    def lines_of(txt, size, bold, w):
        font(size, bold)
        return pdf.multi_cell(w, size * 1.18, txt, dry_run=True, output="LINES")

    cw = W - 2 * m
    center = d.get("layout") == "center"
    al = "C" if center else "L"
    y = m

    # ---------- top row: logo + tag
    top_h = 0
    if logo:
        try:
            buf, lw, lh = _logo(logo, round(S * 0.10))
            hh = S * 0.085
            ww = hh * lw / lh
            pdf.image(buf, x=m, y=y, w=ww, h=hh)
            top_h = hh
        except Exception:
            pass
    tag = _clean_inline((d.get("tag") or "").strip()).upper()[:40]
    if tag:
        size = S * 0.026
        font(size, True)
        tw = pdf.get_string_width(tag) + size * 1.6
        ph = size * 2.1
        tx = W - m - tw if logo and top_h else ((W - tw) / 2 if center else m)
        ty = y + (top_h - ph) / 2 if top_h else y
        pdf.set_fill_color(*acc)
        pdf.rect(tx, ty, tw, ph, style="F", round_corners=True, corner_radius=ph * 0.42)
        on_acc = (20, 20, 20) if _lum(acc) > 0.55 else (255, 255, 255)
        pdf.set_text_color(*on_acc)
        pdf.set_xy(tx, ty)
        pdf.cell(tw, ph, tag, align="C")
        top_h = max(top_h, ph)
    y += top_h + (S * 0.05 if top_h else 0)

    # ---------- footer band
    footer = _clean_inline((d.get("footer") or "").strip())[:160]
    foot_h = 0
    if footer:
        fsize = S * 0.03
        flines = lines_of(footer, fsize, True, cw - fsize * 2)[:2]
        foot_h = len(flines) * fsize * 1.18 + fsize * 2
        fy = H - m - foot_h
        pdf.set_fill_color(*acc)
        pdf.rect(m, fy, cw, foot_h, style="F", round_corners=True, corner_radius=S * 0.02)
        on_acc = (20, 20, 20) if _lum(acc) > 0.55 else (255, 255, 255)
        pdf.set_text_color(*on_acc)
        font(fsize, True)
        pdf.set_xy(m + fsize, fy + fsize)
        pdf.multi_cell(cw - fsize * 2, fsize * 1.18, "\n".join(flines), align="C")
    bottom = H - m - (foot_h + S * 0.045 if foot_h else 0)

    # ---------- middle: headline, subheadline, points (shrunk until they fit)
    headline = _clean_inline((d.get("headline") or "").strip())[:160] or "Your headline"
    sub = _clean_inline((d.get("subheadline") or "").strip())[:300]
    points = [re.sub(r"^\s*[-•*]\s*", "", _clean_inline(p)).strip()
              for p in (d.get("details") or "").split("\n") if p.strip()][:8]
    land = W > H * 1.2
    tall = H > W * 1.5  # stories: more room, bigger words
    scale = 1.25 if tall else 1.0
    for _ in range(25):
        hs = S * (0.105 if not land else 0.085) * scale
        ss = S * 0.042 * scale
        ps = S * 0.034 * scale
        hl = lines_of(headline, hs, True, cw)
        sl = lines_of(sub, ss, False, cw) if sub else []
        pl = [lines_of(p, ps, False, cw - ps * 1.6) for p in points]
        block = (len(hl) * hs * 1.18 + (ss * 0.8 + len(sl) * ss * 1.18 if sl else 0)
                 + (ps * 1.6 + sum(len(x) * ps * 1.18 + ps * 0.55 for x in pl) if pl else 0) + S * 0.035)
        if block <= bottom - y and len(hl) <= 4:
            break
        scale *= 0.92
    # portrait posters look better with the text a little above the centre
    free = (bottom - y) - block
    y += max(0, free * (0.42 if H > W else 0.5))

    pdf.set_text_color(*text_c)
    font(hs, True)
    pdf.set_xy(m, y)
    pdf.multi_cell(cw, hs * 1.18, "\n".join(hl), align=al)
    y = pdf.get_y()
    # accent bar
    pdf.set_fill_color(*acc)
    bar_x = (W - S * 0.11) / 2 if center else m
    pdf.rect(bar_x, y + S * 0.012, S * 0.11, S * 0.011, style="F", round_corners=True, corner_radius=S * 0.005)
    y += S * 0.035
    if sl:
        y += ss * 0.3
        pdf.set_text_color(*muted_c)
        font(ss)
        pdf.set_xy(m, y)
        pdf.multi_cell(cw, ss * 1.18, "\n".join(sl), align=al)
        y = pdf.get_y()
    if pl:
        y += ps * 1.1
        for lines in pl:
            if center:  # centred posters: points as centred lines, no dots
                pdf.set_text_color(*text_c)
                font(ps)
                pdf.set_xy(m, y)
                pdf.multi_cell(cw, ps * 1.18, "\n".join(lines), align="C")
                y = pdf.get_y() + ps * 0.55
                continue
            pdf.set_fill_color(*acc)
            dot = ps * 0.38
            pdf.ellipse(m + ps * 0.1, y + ps * 0.62 - dot / 2, dot, dot, style="F")
            pdf.set_text_color(*text_c)
            font(ps)
            pdf.set_xy(m + ps * 1.6, y)
            pdf.multi_cell(cw - ps * 1.6, ps * 1.18, "\n".join(lines))
            y = pdf.get_y() + ps * 0.55

    pdf_bytes = bytes(pdf.output())

    import pypdfium2 as pdfium

    page = pdfium.PdfDocument(pdf_bytes)[0]
    img = page.render(scale=1).to_pil().convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue(), pdf_bytes
