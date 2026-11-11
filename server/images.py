"""Smaller copies of the user's photos, so the sites the agents build load fast on phones.

For every photo or image the user supplied (not the logo), the server writes WebP copies next to it in assets/:
<name>-480.webp, <name>-960.webp and <name>-1600.webp (only the widths smaller than the original), plus <name>.webp at the
original size (at most 2400px) when the original isn't WebP. The photo is turned upright first and saved without its
metadata (camera details, GPS location). The original stays untouched as the fallback. The agents are told about the
copies in <project_assets> and use them in srcset.
"""

import logging
from pathlib import Path

from PIL import Image, ImageOps

log = logging.getLogger("agents-atelier.images")

WIDTHS = (480, 960, 1600)
FULL_MAX = 2400
QUALITY = 80
KINDS = {"photo", "image"}
SOURCE_EXT = {".jpg", ".jpeg", ".png", ".webp"}     # GIFs may be animated and SVGs are already small: left alone


def make_variants(path: Path) -> list[dict]:
    """Write the WebP copies of one image. Returns [{"path": "assets/x-480.webp", "width": 480}, …], smallest first."""
    path = Path(path)
    if path.suffix.lower() not in SOURCE_EXT:
        return []
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
        width = im.width
        sizes = [w for w in WIDTHS if w < width * 0.9]
        if path.suffix.lower() != ".webp":
            sizes.append(min(width, FULL_MAX))
        out = []
        for w in sizes:
            name = f"{path.stem}-{w}.webp" if w in WIDTHS and w < width * 0.9 else f"{path.stem}.webp"
            copy = im if w == width else im.resize((w, round(im.height * w / width)), Image.LANCZOS)
            copy.save(path.with_name(name), "WEBP", quality=QUALITY, method=6)
            out.append({"path": f"assets/{name}", "width": w})
    return sorted(out, key=lambda v: v["width"])


def ensure(meta: dict, site: Path) -> bool:
    """Make the missing copies for every photo in meta["assets"] (changes meta in place). True if anything changed."""
    changed = False
    for a in meta.get("assets") or []:
        if a.get("kind") not in KINDS or "variants" in a or not (site / a["path"]).is_file():
            continue
        try:
            a["variants"] = make_variants(site / a["path"])
        except Exception as e:          # a damaged image never stops a run: the agents use the original
            log.warning("Couldn't make WebP copies of %s: %s", a["path"], e)
            a["variants"] = []
        changed = True
    return changed


def describe(asset: dict) -> str:
    """The copies, for the asset's line in <project_assets>."""
    v = asset.get("variants") or []
    return ("; smaller WebP copies: " + ", ".join(f"{x['path']} ({x['width']}w)" for x in v)) if v else ""
