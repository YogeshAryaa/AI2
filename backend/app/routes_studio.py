"""Studio: read files (OCR), build resumes and posters. All on this server, no AI service is called."""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, ValidationError

from . import local_image, media, poster, reader, resume, styles
from .auth import Principal, require
from .config import cfg
from .db import db
from .media import _slug, save_file

log = logging.getLogger("rag.studio")
router = APIRouter(prefix="/studio", tags=["studio"])
MAX_IMAGE_MB = 15


async def _bytes(f: UploadFile | None, max_mb: int) -> bytes | None:
    if not f or not f.filename:
        return None
    data = await f.read()
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(413, f"{f.filename} is larger than {max_mb} MB")
    return data or None


def _model(cls, raw: str):
    try:
        return cls.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as e:
        raise HTTPException(422, f"Invalid data: {e}")


@router.get("/options")
async def options(p: Principal = Depends(require("chat"))):
    return {
        "sizes": [{"id": k, "label": v[2], "width": v[0], "height": v[1]} for k, v in poster.SIZES.items()],
        "themes": [{"id": k, "colors": [*v["bg"], v["accent"]]} for k, v in poster.THEMES.items()],
        "resume_templates": [{"id": "classic", "label": "Classic"}, {"id": "modern", "label": "Modern (sidebar)"}],
        "readable": list(reader.READABLE),
        "ocr_languages": cfg.ocr_languages,
        **styles.options(),
        "image_providers": media.image_chain(),
        "local_image_installed": local_image.installed(),
    }


@router.post("/read")
async def read_file(file: UploadFile = File(...), p: Principal = Depends(require("chat"))):
    """Text of a PDF / Word / image (OCR for images and scanned PDFs), plus resume fields guessed from it."""
    data = await _bytes(file, cfg.max_upload_mb)
    if not data:
        raise HTTPException(400, "Empty file")
    try:
        res = await asyncio.to_thread(reader.read, file.filename or "file", data)
    except reader.ReadError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        log.exception("read failed")
        raise HTTPException(500, f"Could not read the file: {type(e).__name__}: {e}")
    text = res["text"]
    return {"filename": file.filename, **res, "chars": len(text), "words": len(text.split()),
            "resume": resume.parse(text) if text.strip() else None}


class Section(BaseModel):
    heading: str = Field(default="", max_length=80)
    body: str = Field(default="", max_length=8000)


class ResumeIn(BaseModel):
    name: str = Field(default="", max_length=120)
    headline: str = Field(default="", max_length=160)
    email: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=60)
    location: str = Field(default="", max_length=120)
    links: str = Field(default="", max_length=500)
    summary: str = Field(default="", max_length=3000)
    skills: str = Field(default="", max_length=2000)
    sections: list[Section] = Field(default=[], max_length=20)
    template: str = Field(default="classic", pattern="^(classic|modern)$")
    accent: str = Field(default="#2563eb", pattern="^#[0-9a-fA-F]{6}$")


@router.post("/resume")
async def make_resume(data: str = Form(...), photo: UploadFile | None = File(None),
                      p: Principal = Depends(require("chat"))):
    d = _model(ResumeIn, data)
    img = await _bytes(photo, MAX_IMAGE_MB)
    try:
        pdf = await asyncio.to_thread(resume.build_pdf, d.model_dump(), d.template, d.accent, img)
    except Exception as e:
        log.exception("resume failed")
        raise HTTPException(500, f"Could not create the resume: {type(e).__name__}: {e}")
    f = await save_file(p.key_id, None, None, "pdf", _slug(f"{d.name or 'resume'} resume", "pdf"), "application/pdf", pdf)
    return {"files": [f]}


class PosterIn(BaseModel):
    size: str = Field(default="square", pattern="^(square|story|a4|landscape)$")
    theme: str = Field(default="ocean", max_length=20)
    accent: str = Field(default="", pattern="^(#[0-9a-fA-F]{6})?$")
    tag: str = Field(default="", max_length=40)
    headline: str = Field(default="", max_length=160)
    subheadline: str = Field(default="", max_length=300)
    details: str = Field(default="", max_length=1200)
    footer: str = Field(default="", max_length=160)
    layout: str = Field(default="left", pattern="^(left|center)$")


@router.post("/poster")
async def make_poster(data: str = Form(...), background: UploadFile | None = File(None),
                      logo: UploadFile | None = File(None), p: Principal = Depends(require("chat"))):
    d = _model(PosterIn, data)
    if d.theme not in poster.THEMES:
        raise HTTPException(422, f"Unknown theme. Use one of: {', '.join(poster.THEMES)}")
    bg = await _bytes(background, MAX_IMAGE_MB)
    lg = await _bytes(logo, MAX_IMAGE_MB)
    try:
        png, pdf = await asyncio.to_thread(poster.build, d.model_dump(), bg, lg)
    except Exception as e:
        log.exception("poster failed")
        raise HTTPException(500, f"Could not create the poster: {type(e).__name__}: {e}")
    name = d.headline or "poster"
    return {"files": [
        await save_file(p.key_id, None, None, "image", _slug(name, "png"), "image/png", png),
        await save_file(p.key_id, None, None, "pdf", _slug(name, "pdf"), "application/pdf", pdf),
    ]}


IMAGE_SIZES = {"square": (1024, 1024), "portrait": (768, 1024), "landscape": (1024, 576), "story": (576, 1024)}


class ImageIn(BaseModel):
    prompt: str = Field(min_length=2, max_length=1000)
    style: str = Field(default="auto", max_length=20)
    colors: list[str] = Field(default=[], max_length=3)
    size: str = Field(default="square", pattern="^(square|portrait|landscape|story)$")
    provider: str = Field(default="auto", pattern="^(auto|local|online)$")
    enhance: bool = True  # let the text AI turn a short / Hindi request into a detailed English prompt
    count: int = Field(default=1, ge=1, le=4)  # several designs: with style "auto" each gets a different style


@router.post("/image")
async def make_image(body: ImageIn, p: Principal = Depends(require("chat"))):
    """AI image with a chosen style, colours and size. provider: auto (configured order: own AI first),
    local (only the model on this server) or online (only the online services)."""
    if body.style not in styles.IMAGE_STYLES:
        raise HTTPException(422, f"Unknown style. Use one of: {', '.join(styles.IMAGE_STYLES)}")
    base = await media._image_prompt(body.prompt, []) if body.enhance else body.prompt
    w, h = IMAGE_SIZES[body.size]
    explicit = None if body.style == "auto" else body.style
    recipe = {"base": base, "colors": [c for c in body.colors if c in styles.COLORS], "w": w, "h": h,
              "prefer": None if body.provider == "auto" else body.provider, "name": body.prompt[:50]}
    files, errors = [], []
    async for kind, val in media._make_images(recipe, media._styles_for(body.count, None, explicit, body.count > 1),
                                              p.key_id, None, None):
        (files if kind == "file" else errors).append(val)
    if not files:
        raise HTTPException(502, "Could not create the image: " + " | ".join(errors)[:600])
    return {"files": files, "errors": errors, "prompt": base}


# ---------------------------------------------------------------- gallery: images made earlier stay visible
def _studio_images(p: Principal) -> dict:
    """AI images this key made in the Studio (chat images belong to their conversation)."""
    return {"owner": p.key_id, "conversation_id": None, "kind": "image", "recipe.prompt": {"$exists": True}}


@router.get("/images")
async def my_images(limit: int = 60, skip: int = 0, p: Principal = Depends(require("chat"))):
    cur = (db.rag_files.find(_studio_images(p), {"data": 0}).sort("created_at", -1)
           .skip(max(0, skip)).limit(max(1, min(limit, 200))))
    return [{
        "id": d["_id"], "kind": d["kind"], "name": d["name"], "mime": d["mime"], "size": d["size"],
        "url": f"/files/{d['_id']}", "label": d.get("label"), "created_at": d["created_at"],
        "prompt": (d.get("recipe") or {}).get("prompt"),
    } async for d in cur]


@router.delete("/images/{fid}")
async def delete_image(fid: str, p: Principal = Depends(require("chat"))):
    res = await db.rag_files.delete_one({"_id": fid, **_studio_images(p)})
    if not res.deleted_count:
        raise HTTPException(404, "Image not found")
    return {"deleted": True}
