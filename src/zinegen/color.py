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


def profile_components(path: Path) -> list[str]:
    """CSS @color-profile component names for the profile's colour space (c,m,y,k or r,g,b or gray)."""
    space = ImageCms.ImageCmsProfile(str(path)).profile.xcolor_space.strip()
    return {"CMYK": ["c", "m", "y", "k"], "RGB": ["r", "g", "b"], "GRAY": ["gray"]}.get(space, ["c", "m", "y", "k"])


def image_info(path: Path) -> dict:
    with Image.open(path) as im:
        icc = im.info.get("icc_profile")
        desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip() if icc else None
        return {"width": im.width, "height": im.height, "mode": im.mode, "icc": desc}


CMYK_ALGO = "v2:lanczos:bpc"   # bump when the conversion itself changes, so old cache entries are not reused


def cmyk_key(src: Path, profile: Path, intent: str, quality: int, size: tuple[int, int] | None) -> str:
    """Cache key of a CMYK conversion: everything that changes the output bytes.

    The whole source file is hashed (a re-fetched rendition with the same first MiB must not hit the cache),
    plus the profile bytes, intent, JPEG quality, the target pixel size (None = source size) and the algorithm.
    """
    h = hashlib.sha1()
    h.update(src.read_bytes())
    h.update(profile.read_bytes())
    target = "source" if size is None else f"{size[0]}x{size[1]}"
    h.update(f"{CMYK_ALGO}:{intent}:{quality}:{target}".encode())
    return h.hexdigest()[:16]


def to_cmyk(src: Path, profile: Path, dest_dir: Path, intent: str = "perceptual", quality: int = 95,
            log=None, size: tuple[int, int] | None = None) -> Path:
    """Convert `src` (RGB JPEG with embedded ICC, else assumed sRGB) to a CMYK JPEG in `dest_dir`.

    `size` (width, height) resamples the decoded source pixels before the colour transform; the result is
    JPEG-encoded exactly once, so the PDF/X-1a image is still a single lossy generation from the source.
    `None` or the source size keeps every pixel. Never upsamples. Cached by `cmyk_key`. Returns the file path.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        src_size = im.size
    if size is not None:
        size = (min(int(size[0]), src_size[0]), min(int(size[1]), src_size[1]))
        if size == src_size:
            size = None
    key = cmyk_key(src, profile, intent, quality, size)
    out = dest_dir / f"{src.stem}.{key}.cmyk.jpg"
    if out.exists():
        return out
    with Image.open(src) as im:
        im.load()
        icc = im.info.get("icc_profile")
        inp = ImageCms.ImageCmsProfile(io.BytesIO(icc)) if icc else ImageCms.createProfile("sRGB")
        if im.mode != "RGB":
            im = im.convert("RGB")
        if size is not None:
            im = im.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0)
        prof = ImageCms.ImageCmsProfile(str(profile))
        tr = ImageCms.buildTransform(inp, prof, "RGB", "CMYK", renderingIntent=INTENTS[intent],
                                     flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
        cmyk = ImageCms.applyTransform(im, tr)
        tmp = out.with_name(out.name + ".part")
        cmyk.save(tmp, format="JPEG", quality=quality, subsampling=0, icc_profile=prof.tobytes(), dpi=(300, 300))
        tmp.replace(out)
    meta = {"source": str(src), "source_px": list(src_size), "px": list(size or src_size),
            "profile": profile_description(profile), "intent": intent, "quality": quality, "algo": CMYK_ALGO}
    out.with_suffix(".json").write_text(json.dumps(meta))
    if log:
        log(f"cmyk {src.name} {src_size[0]}x{src_size[1]} -> {out.name}" +
            (f" ({size[0]}x{size[1]})" if size else ""))
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
