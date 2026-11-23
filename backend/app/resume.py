"""Resume builder, with no AI: rules read an old resume into fields, the user edits them, fpdf2 draws the PDF.

Section body format (what the editor shows and the PDF understands):

    Senior React Developer | Infosys | 2021 – Present     <- entry line: bold title, last part on the right
    - Built the hiring portal used by 40 recruiters       <- bullet
    Plain lines are printed as normal text.
"""
import io
import re

from .media import FONTS, _clean_inline, _pdf_fonts

SECTION_WORDS = {
    "summary": ("summary", "professional summary", "profile", "professional profile", "objective", "career objective",
                "about me", "about", "overview", "career summary"),
    "skills": ("skills", "technical skills", "key skills", "core skills", "skill set", "skillset", "core competencies",
               "competencies", "technologies", "tech stack", "tools", "it skills", "expertise", "areas of expertise"),
    "experience": ("experience", "work experience", "professional experience", "employment", "employment history",
                   "work history", "career history", "internships", "internship", "relevant experience"),
    "projects": ("projects", "key projects", "personal projects", "academic projects", "project details"),
    "education": ("education", "educational qualification", "educational qualifications", "qualification",
                  "qualifications", "academic qualification", "academics", "academic details", "education details"),
    "certifications": ("certifications", "certification", "certificates", "courses", "trainings", "training",
                       "licenses"),
    "achievements": ("achievements", "awards", "honors", "honours", "accomplishments", "awards and achievements"),
    "languages": ("languages", "languages known"),
    "interests": ("interests", "hobbies", "hobbies and interests"),
    "personal": ("personal details", "personal information", "personal info", "declaration"),
}
TITLES = {"experience": "Experience", "projects": "Projects", "education": "Education",
          "certifications": "Certifications", "achievements": "Achievements", "languages": "Languages",
          "interests": "Interests", "personal": "Personal details"}
_HEADING_OF = {w: k for k, words in SECTION_WORDS.items() for w in words}

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)?\d{3,5}[\s-]?\d{4,5}")
LINK = re.compile(r"(?:https?://)?(?:www\.)?(?:linkedin\.com|github\.com|gitlab\.com|behance\.net|dribbble\.com|"
                  r"[\w-]+\.(?:dev|io|me|in|com|net|org)/?)[^\s,|]*", re.I)
BULLET = re.compile(r"^\s*[-•●▪■◦○*·➢➤►▶✓✔❖]\s*")


def _heading(line: str) -> str | None:
    s = re.sub(r"[:\-–—_|•]+$", "", line.strip()).strip().lower()
    s = re.sub(r"\s+", " ", s)
    if not s or len(s) > 40:
        return None
    return _HEADING_OF.get(s) or _HEADING_OF.get(s.replace("&", "and"))


def _is_contact(line: str) -> bool:
    return bool(EMAIL.search(line) or LINK.search(line) or len(re.sub(r"\D", "", line)) >= 10)


def parse(text: str) -> dict:
    """Best-effort fields from resume text. The user checks and edits them before the PDF is made."""
    lines = [ln.rstrip() for ln in text.replace("\r", "").split("\n")]
    out = {"name": "", "headline": "", "email": "", "phone": "", "location": "", "links": "", "summary": "",
           "skills": "", "sections": []}

    m = EMAIL.search(text)
    out["email"] = m.group(0).rstrip(".") if m else ""
    for m in PHONE.finditer(text):
        if len(re.sub(r"\D", "", m.group(0))) >= 10:
            out["phone"] = m.group(0).strip()
            break
    links = []
    for m in LINK.finditer(text):
        v = m.group(0).rstrip(".,;)")
        if "@" not in v and v.lower() not in (x.lower() for x in links) and not EMAIL.search(v) \
                and re.search(r"linkedin|github|gitlab|behance|dribbble|portfolio|https?://", v, re.I):
            links.append(v)
    out["links"] = ", ".join(links[:4])

    # split into blocks by known headings
    head, blocks, cur = [], {}, None
    order = []
    for ln in lines:
        key = _heading(ln)
        if key:
            cur = key
            if key not in blocks:
                blocks[key] = []
                order.append(key)
            continue
        (blocks[cur] if cur else head).append(ln)

    # name + headline from the top lines (before any heading)
    top = [ln.strip() for ln in head if ln.strip()]
    for i, ln in enumerate(top[:6]):
        words = ln.split()
        if not _is_contact(ln) and 1 < len(words) <= 5 and re.fullmatch(r"[A-Za-zऀ-ॿ .'-]+", ln):
            out["name"] = ln.title() if ln.isupper() else ln
            rest = top[i + 1:i + 3]
            if rest and not _is_contact(rest[0]) and len(rest[0]) <= 70:
                out["headline"] = rest[0]
            break
    loc = next((ln for ln in top[:8] if re.search(r"\b(india|delhi|mumbai|bangalore|bengaluru|pune|hyderabad|"
                                                   r"chennai|kolkata|noida|gurgaon|gurugram|ahmedabad|jaipur|"
                                                   r"lucknow|indore|remote)\b", ln, re.I)), "")
    if loc:
        parts = [p.strip() for p in re.split(r"[|•,]", loc) if re.search(r"[A-Za-z]", p) and not _is_contact(p)]
        out["location"] = ", ".join(parts[:2])[:60]
    leftover = [ln for ln in top if ln not in (out["name"], out["headline"]) and not _is_contact(ln)
                and ln != loc]

    def clean(block: list[str]) -> str:
        res, blank = [], False
        for ln in block:
            if not ln.strip():
                blank = bool(res)
                continue
            if blank:
                res.append("")
                blank = False
            res.append(BULLET.sub("- ", ln.strip()) if BULLET.match(ln) else ln.strip())
        return "\n".join(res).strip()

    out["summary"] = clean(blocks.pop("summary", [])) or (" ".join(leftover) if 40 < len(" ".join(leftover)) < 900 else "")
    if "skills" in blocks:
        raw = clean(blocks.pop("skills"))
        items = [BULLET.sub("", x).strip() for x in re.split(r"[,\n|•;]", raw)]
        items = [re.sub(r"^[^:]{1,30}:\s*", "", x) if ":" in x else x for x in items]  # "Frontend: React" -> React
        seen, skills = set(), []
        for x in items:
            if x and len(x) <= 40 and x.lower() not in seen:
                seen.add(x.lower())
                skills.append(x)
        out["skills"] = ", ".join(skills)
    for key in order:
        if key in blocks and key != "personal":
            body = clean(blocks[key])
            if body:
                out["sections"].append({"heading": TITLES.get(key, key.title()), "body": body})
    return out


# ------------------------------------------------------------------ PDF
def _hex(c: str, default=(37, 99, 235)) -> tuple[int, int, int]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (c or "").strip())
    return tuple(int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if m else default


def _entry_parts(line: str) -> tuple[str, str, str] | None:
    """'Role | Company | 2021 - Present' -> (Role, Company, 2021 - Present)."""
    if "|" not in line:
        return None
    parts = [p.strip() for p in line.split("|") if p.strip()]
    if len(parts) < 2:
        return None
    date = parts[-1] if re.search(r"\d{4}|present|current|now|till|abhi", parts[-1], re.I) else ""
    rest = parts[:-1] if date else parts
    return rest[0], " · ".join(rest[1:]), date


def build_pdf(d: dict, template: str = "classic", accent: str = "#2563eb", photo: bytes | None = None) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    acc = _hex(accent)
    ink, muted = (25, 30, 40), (100, 108, 122)
    modern = template == "modern"
    side_w = 66 if modern else 0
    tint = tuple(round(c * 0.12 + 255 * 0.88) for c in acc)

    class Doc(FPDF):
        def header(self):
            if modern:  # sidebar colour on every page
                self.set_fill_color(*tint)
                self.rect(0, 0, side_w, self.h, style="F")

    pdf = Doc(format="A4")
    pdf.set_title(f"{d.get('name') or 'Resume'} - Resume")
    pdf.set_creator("Ask my data")
    _pdf_fonts(pdf)
    pdf.set_auto_page_break(True, margin=14)

    def text(s, size=10, style="", color=ink, h=None, w=0.0, x=None, align="L"):
        pdf.set_font("Noto", style, size)
        pdf.set_text_color(*color)
        if x is not None:
            pdf.set_x(x)
        pdf.multi_cell(w or (pdf.w - pdf.r_margin - pdf.get_x()), h or size * 0.5, _clean_inline(s),
                       markdown=True, align=align, new_x=XPos.LEFT, new_y=YPos.NEXT)

    def section_title(title, x, w, color=acc, line=True):
        pdf.ln(3.5)
        pdf.set_x(x)
        text(title.upper(), 10.5, "B", color, w=w, x=x)
        if line:
            y = pdf.get_y() + 0.6
            pdf.set_draw_color(*color)
            pdf.set_line_width(0.4)
            pdf.line(x, y, x + w, y)
        pdf.ln(2.2)

    def body(content: str, x: float, w: float):
        for raw in content.split("\n"):
            s = raw.strip()
            if not s:
                pdf.ln(1.8)
                continue
            ent = _entry_parts(s)
            if ent:
                title, org, date = ent
                y0 = pdf.get_y()
                if pdf.will_page_break(10):
                    pdf.add_page()
                    pdf.set_x(x)
                    y0 = pdf.get_y()
                if date:
                    pdf.set_font("Noto", "", 9)
                    pdf.set_text_color(*muted)
                    dw = pdf.get_string_width(date) + 1
                    pdf.set_xy(x + w - dw, y0)
                    pdf.cell(dw, 5.2, date, align="R")
                else:
                    dw = 0
                pdf.set_xy(x, y0)
                text(f"**{title}**" + (f"  ·  {org}" if org else ""), 10.5, w=w - dw - 2, x=x, h=5.2)
                pdf.ln(0.6)
            elif BULLET.match(s):
                pdf.set_x(x + 1.5)
                pdf.set_font("Noto", "", 10)
                pdf.set_text_color(*acc)
                y0 = pdf.get_y()
                pdf.cell(4, 5, "•")
                pdf.set_xy(x + 5.5, y0)
                text(BULLET.sub("", s), 10, w=w - 5.5, x=x + 5.5, h=5)
                pdf.ln(0.4)
            else:
                text(s, 10, w=w, x=x, h=5)
                pdf.ln(0.4)

    def contacts() -> list[str]:
        return [v for v in (d.get("email"), d.get("phone"), d.get("location"),
                            *[x.strip() for x in (d.get("links") or "").split(",")]) if v and v.strip()]

    skills = [s.strip() for s in re.split(r"[,\n]", d.get("skills") or "") if s.strip()]

    if not modern:
        pdf.set_margins(17, 15, 17)
        pdf.add_page()
        pdf.set_fill_color(*acc)
        pdf.rect(0, 0, pdf.w, 4, style="F")
        pdf.set_y(14)
        text(d.get("name") or "Your Name", 24, "B", ink, h=11)
        if d.get("headline"):
            text(d["headline"], 12, "", acc, h=6.5)
        if contacts():
            pdf.ln(1)
            text("   |   ".join(contacts()), 9, "", muted, h=5)
        x, w = pdf.l_margin, pdf.epw
        if d.get("summary"):
            section_title("Profile", x, w)
            body(d["summary"], x, w)
        if skills:
            section_title("Skills", x, w)
            _chips(pdf, skills, x, w, acc)
        for sec in d.get("sections") or []:
            if sec.get("body", "").strip():
                section_title(sec.get("heading") or "", x, w)
                body(sec["body"], x, w)
    else:
        # main column first (it may run over several pages), then the sidebar on page 1
        x, w = side_w + 10, pdf.w - side_w - 24
        pdf.set_margins(x, 14, 14)
        pdf.add_page()
        pdf.set_xy(x, 14)
        text(d.get("name") or "Your Name", 23, "B", ink, h=10.5, w=w, x=x)
        if d.get("headline"):
            text(d["headline"], 12, "", acc, h=6.5, w=w, x=x)
        if d.get("summary"):
            section_title("Profile", x, w)
            body(d["summary"], x, w)
        for sec in d.get("sections") or []:
            if sec.get("body", "").strip():
                section_title(sec.get("heading") or "", x, w)
                body(sec["body"], x, w)

        last = pdf.page
        pdf.page = 1
        pdf.set_auto_page_break(False)
        sx, sw = 8, side_w - 16
        pdf.set_left_margin(sx)
        y = 14
        if photo:
            try:
                from PIL import Image, ImageDraw, ImageOps

                img = ImageOps.fit(ImageOps.exif_transpose(Image.open(io.BytesIO(photo))).convert("RGB"), (480, 480))
                mask = Image.new("L", img.size, 0)
                ImageDraw.Draw(mask).ellipse((0, 0, *img.size), fill=255)
                img.putalpha(mask)
                buf = io.BytesIO()
                img.save(buf, "PNG")
                size = 36
                pdf.image(buf, x=(side_w - size) / 2, y=y, w=size, h=size)
                y += size + 4
            except Exception:
                pass
        pdf.set_xy(sx, y)
        bottom = pdf.h - 12
        if contacts():
            section_title("Contact", sx, sw, line=False)
            for c in contacts():
                if pdf.get_y() > bottom - 6:
                    break
                text(c, 8.8, "", ink, w=sw, x=sx, h=4.6)
                pdf.ln(1.2)
        if skills and pdf.get_y() < bottom - 20:
            section_title("Skills", sx, sw, line=False)
            for sk in skills:
                if pdf.get_y() > bottom - 6:
                    break
                text(f"•  {sk}", 9, "", ink, w=sw, x=sx, h=4.8)
                pdf.ln(0.4)
        pdf.page = last
    return bytes(pdf.output())


def _chips(pdf, items: list[str], x: float, w: float, acc) -> None:
    pdf.set_font("Noto", "", 9.2)
    tint = [round(c * 0.12 + 255 * 0.88) for c in acc]
    cx, cy, h, gap = x, pdf.get_y(), 6.2, 2
    for s in items:
        sw = pdf.get_string_width(s) + 6
        if cx + sw > x + w:
            cx, cy = x, cy + h + gap
        if cy + h > pdf.h - pdf.b_margin:
            pdf.add_page()
            cx, cy = x, pdf.get_y()
        pdf.set_fill_color(*tint)
        pdf.set_draw_color(*tint)
        pdf.rect(cx, cy, sw, h, style="F", round_corners=True, corner_radius=1.8)
        pdf.set_text_color(25, 30, 40)
        pdf.set_xy(cx, cy)
        pdf.cell(sw, h, s, align="C")
        cx += sw + gap
    pdf.set_xy(x, cy + h + 1)


__all__ = ["parse", "build_pdf", "FONTS"]
