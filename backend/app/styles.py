"""Colours and image styles, shared by chat PDFs, images, posters and resumes.

In the chat people simply say it: "green theme mein PDF bana do", "laal rang ka poster",
"cartoon style image banao", "realistic photo of an office". Studio shows the same lists as buttons.
"""
import re

# id -> (hex, words that ask for it: English, Hinglish, Hindi)
COLORS = {
    "blue": ("#2563eb", r"blue|neela|nila|neeli|nili|नीला|नीले|नीली"),
    "navy": ("#1e3a8a", r"navy|dark\s*blue|gehra\s*neela"),
    "green": ("#16a34a", r"green|hara|hari|hare|हरा|हरी|हरे"),
    "teal": ("#0d9488", r"teal|firozi|turquoise|फ़िरोज़ी|फिरोजी"),
    "red": ("#dc2626", r"red|laal|lal|लाल"),
    "orange": ("#ea580c", r"orange|narangi|kesariya|saffron|नारंगी|केसरिया"),
    "yellow": ("#ca8a04", r"yellow|peela|pila|peeli|पीला|पीली"),
    "gold": ("#b45309", r"gold|golden|sunehra|sunahra|सुनहरा"),
    "pink": ("#db2777", r"pink|gulabi|गुलाबी"),
    "purple": ("#7c3aed", r"purple|violet|baingani|jamuni|बैंगनी|जामुनी"),
    "brown": ("#92400e", r"brown|bhura|भूरा"),
    "black": ("#111827", r"black|kala|kaala|काला"),
    "grey": ("#475569", r"grey|gray|slate|सलेटी"),
    "white": ("#f8fafc", r"white|safed|safaid|सफेद|सफ़ेद"),
}
DEFAULT_PDF_COLOR = "blue"
_COLOR_RE = {k: re.compile(rf"(?<![\wऀ-ॿ])({w})(?![\wऀ-ॿ])", re.I) for k, (_, w) in COLORS.items()}
# words around a colour request that should not end up in a search query or an image subject
THEME_WORDS = re.compile(r"\b(theme|colou?r|colou?red|rang|ranga|tone|shade|style|design|look)\b|रंग|थीम", re.I)

# id -> (label, words that ask for it, text added to the image prompt)
IMAGE_STYLES = {
    "auto": ("Auto", None, ""),
    "realistic": ("Realistic photo", r"realistic|real|photo\s*realistic|photorealistic|asli|असली|real\s*photo|photograph",
                  "photorealistic photograph, natural lighting, sharp focus, high detail"),
    "illustration": ("Flat illustration", r"flat|vector|illustration|infographic",
                     "flat vector illustration, clean shapes, soft gradients, modern"),
    "cartoon": ("Cartoon", r"cartoon|kartoon|कार्टून|comic", "cartoon style, bold outlines, bright cheerful colours"),
    "3d": ("3D render", r"3d|3-d|three\s*d", "3D render, soft studio lighting, smooth materials, depth of field"),
    "anime": ("Anime", r"anime|manga", "anime style, clean line art, vibrant colours"),
    "watercolor": ("Watercolour", r"water\s*colou?r|watercolou?r", "watercolour painting, soft washes, paper texture"),
    "sketch": ("Pencil sketch", r"sketch|pencil|drawing|line\s*art|skech", "detailed pencil sketch, graphite shading, white paper"),
    "oil": ("Oil painting", r"oil\s*painting|painting|canvas", "oil painting, rich textured brush strokes"),
    "logo": ("Logo", r"logo|icon|emblem|लोगो", "minimal flat vector logo, simple geometric shapes, centered, plain background"),
    "poster": ("Poster art", r"poster|flyer|banner|पोस्टर", "bold graphic poster design, strong composition, eye-catching"),
    "cinematic": ("Cinematic", r"cinematic|movie|film", "cinematic shot, dramatic lighting, wide angle, film still"),
    "pixel": ("Pixel art", r"pixel|8\s*bit|retro\s*game", "pixel art, 16-bit retro game style"),
}
_STYLE_RE = {k: re.compile(rf"\b({w})\b", re.I) for k, (_, w, _) in IMAGE_STYLES.items() if w}


def find_color(text: str) -> str | None:
    """'green theme mein PDF bana do' -> 'green'. The earliest colour named wins."""
    hits = [(m.start(), k) for k, rx in _COLOR_RE.items() if (m := rx.search(text))]
    return min(hits)[1] if hits else None


def find_colors(text: str, limit: int = 3) -> list[str]:
    hits = sorted((m.start(), k) for k, rx in _COLOR_RE.items() if (m := rx.search(text)))
    return [k for _, k in hits][:limit]


def color_hex(name: str | None, default: str = DEFAULT_PDF_COLOR) -> str:
    if name and re.fullmatch(r"#[0-9a-fA-F]{6}", name):
        return name
    return COLORS.get(name or default, COLORS[default])[0]


def find_style(text: str) -> str | None:
    for k, rx in _STYLE_RE.items():
        if rx.search(text):
            return k
    return None


def strip_color_words(text: str) -> str:
    for rx in _COLOR_RE.values():
        text = rx.sub(" ", text)
    return re.sub(r"\s+", " ", THEME_WORDS.sub(" ", text)).strip()


def style_suffix(style: str | None, colors: list[str] | None = None) -> str:
    parts = []
    if style and style in IMAGE_STYLES and IMAGE_STYLES[style][2]:
        parts.append(IMAGE_STYLES[style][2])
    if colors:
        parts.append("colour palette: " + " and ".join(c for c in colors if c in COLORS))
    return ", ".join(parts)


def options() -> dict:
    return {
        "colors": [{"id": k, "hex": v[0]} for k, v in COLORS.items()],
        "image_styles": [{"id": k, "label": v[0]} for k, v in IMAGE_STYLES.items()],
    }
