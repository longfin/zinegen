"""ICC colour conversion of source JPEGs for print (Pillow + LittleCMS), with a per-profile cache."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageCms

Image.MAX_IMAGE_PIXELS = None

ADOBE_PROFILE_DIRS = [
    Path("/Library/Application Support/Adobe/Color/Profiles/Recommended"),
    Path("/Library/Application Support/Adobe/Color/Profiles"),
    Path.home() / "Library/ColorSync/Profiles",
    Path("/Library/ColorSync/Profiles"),
    Path("/usr/share/color/icc"),
]

INTENTS = {
    "perceptual": ImageCms.Intent.PERCEPTUAL,
    "relative-colorimetric": ImageCms.Intent.RELATIVE_COLORIMETRIC,
    "saturation": ImageCms.Intent.SATURATION,
    "absolute-colorimetric": ImageCms.Intent.ABSOLUTE_COLORIMETRIC,
}


def find_profile(name_or_path: str | None) -> Path | None:
    """Resolve an ICC profile by path or by file name searched in the usual directories."""
    if not name_or_path:
        return None
    p = Path(name_or_path).expanduser()
    if p.exists():
        return p
    for d in ADOBE_PROFILE_DIRS:
        for cand in (d / name_or_path, d / f"{name_or_path}.icc", d / f"{name_or_path}.icm"):
            if cand.exists():
                return cand
    return None


def profile_description(path: Path) -> str:
    return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(str(path))).strip()


def image_info(path: Path) -> dict:
    with Image.open(path) as im:
        icc = im.info.get("icc_profile")
        desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip() if icc else None
        return {"width": im.width, "height": im.height, "mode": im.mode, "icc": desc}


def to_cmyk(src: Path, profile: Path, dest_dir: Path, intent: str = "perceptual", quality: int = 95,
            log=None) -> Path:
    """Convert `src` (RGB JPEG with embedded ICC, else assumed sRGB) to a CMYK JPEG in `dest_dir`.

    Cached by (source bytes hash, profile, intent, quality). Returns the CMYK file path.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1()
    h.update(src.read_bytes()[:1 << 20])            # first MiB is plenty to identify the render
    h.update(str(src.stat().st_size).encode())
    h.update(profile.read_bytes())
    h.update(f"{intent}:{quality}".encode())
    key = h.hexdigest()[:16]
    out = dest_dir / f"{src.stem}.{key}.cmyk.jpg"
    if out.exists():
        return out
    with Image.open(src) as im:
        im.load()
        icc = im.info.get("icc_profile")
        inp = ImageCms.ImageCmsProfile(io.BytesIO(icc)) if icc else ImageCms.createProfile("sRGB")
        if im.mode != "RGB":
            im = im.convert("RGB")
        prof = ImageCms.ImageCmsProfile(str(profile))
        tr = ImageCms.buildTransform(inp, prof, "RGB", "CMYK", renderingIntent=INTENTS[intent],
                                     flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
        cmyk = ImageCms.applyTransform(im, tr)
        cmyk.save(out, quality=quality, subsampling=0, icc_profile=prof.tobytes(), dpi=(300, 300))
    meta = {"source": str(src), "profile": profile_description(profile), "intent": intent, "quality": quality}
    out.with_suffix(".json").write_text(json.dumps(meta))
    if log:
        log(f"cmyk {src.name} -> {out.name}")
    return out


def gamut_report(src: Path, profile: Path, scale: int = 8) -> float:
    """Fraction (0..1) of pixels outside the CMYK gamut, relative-colorimetric, on a downscaled copy."""
    with Image.open(src) as im:
        im.load()
        icc = im.info.get("icc_profile")
        inp = ImageCms.ImageCmsProfile(io.BytesIO(icc)) if icc else ImageCms.createProfile("sRGB")
        small = im.convert("RGB").reduce(scale)
        prof = ImageCms.ImageCmsProfile(str(profile))
        t = ImageCms.buildProofTransform(inp, ImageCms.createProfile("sRGB"), prof, "RGB", "RGB",
                                         renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
                                         proofRenderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
                                         flags=ImageCms.Flags.GAMUTCHECK | ImageCms.Flags.SOFTPROOFING)
        g = ImageCms.applyTransform(small, t)
        px = list(g.getdata())
        return sum(1 for p in px if p == (127, 127, 127)) / max(1, len(px))
