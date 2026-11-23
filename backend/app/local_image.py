"""Image AI on this server, no third party: Stable Diffusion Turbo through `diffusers`.

* Free and unlimited, nothing leaves the machine.
* CPU works (a laptop takes about a minute per image); an NVIDIA GPU makes it a few seconds.
* The model (~2.5 GB) downloads once from Hugging Face the first time, or ahead of time with
  `python -c "from app.local_image import warm; warm()"`.
SD-Turbo makes ~512 px images in 1-4 steps; they are upscaled to the requested size.
Check the model's licence before commercial use (IMAGE_LOCAL_MODEL can point to another model).
"""
import io
import logging
import random
import threading

from .config import cfg

log = logging.getLogger("rag.local_image")
_pipe = None
_lock = threading.Lock()


def installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("diffusers") is not None


def _load():
    global _pipe
    if _pipe is None:
        try:
            import torch
            from diffusers import AutoPipelineForText2Image
        except ImportError as e:
            raise RuntimeError("local image AI is not installed (pip install diffusers)") from e
        gpu = torch.cuda.is_available()
        dtype = torch.float16 if gpu else torch.float32
        log.info("Loading local image model %s on %s", cfg.image_local_model, "GPU" if gpu else "CPU")
        try:  # the fp16 files are half the download; on CPU they are converted to float32
            pipe = AutoPipelineForText2Image.from_pretrained(cfg.image_local_model, variant="fp16", torch_dtype=dtype)
        except (OSError, ValueError):
            pipe = AutoPipelineForText2Image.from_pretrained(cfg.image_local_model, torch_dtype=dtype)
        pipe = pipe.to("cuda" if gpu else "cpu")
        pipe.set_progress_bar_config(disable=True)
        try:
            pipe.enable_attention_slicing()  # less RAM, same picture
        except Exception:
            pass
        _pipe = pipe
    return _pipe


def warm() -> None:
    with _lock:
        _load()


def _native(width: int, height: int) -> tuple[int, int]:
    """Same shape as asked, at the model's own ~512 px scale (multiples of 64)."""
    base = cfg.image_local_size
    ar = max(0.5, min(2.0, width / max(1, height)))
    if ar >= 1:
        return round(base * ar / 64) * 64, base
    return base, round(base / ar / 64) * 64


def generate(prompt: str, width: int, height: int) -> bytes:
    """PNG bytes. Blocking (call it in a thread); one image at a time."""
    import torch
    from PIL import Image

    w, h = _native(width, height)
    with _lock:
        pipe = _load()
        gen = torch.Generator("cpu").manual_seed(random.randrange(2**31))
        img = pipe(prompt=prompt, num_inference_steps=max(1, min(cfg.image_local_steps, 8)), guidance_scale=0.0,
                   width=w, height=h, generator=gen).images[0]
    target = max(width, height)
    if max(img.size) < target:
        scale = target / max(img.size)
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
