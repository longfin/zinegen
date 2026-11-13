"""ICC colour conversion of source JPEGs for print (Pillow + LittleCMS), with a per-profile cache."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

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


def find_profile(name_or_path: str | None, base: Path | None = None) -> Path | None:
    """Resolve an ICC profile by path or by file name searched in the usual directories.

    A relative path is resolved against `base` (the book directory, where book.yaml lives) when given, never
    against the process's working directory: `output.icc: profiles/press.icc` means the same file whoever
    renders the book. Without `base` (doctor, tests) it is relative to the working directory, as before.
    """
    if not name_or_path:
        return None
    p = Path(name_or_path).expanduser()
    if base is not None and not p.is_absolute():
        p = Path(base) / p
    if p.is_file():
        return p.resolve()
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


EXIF_ORIENTATION = 0x0112


def orientation(im: Image.Image) -> int:
    """EXIF Orientation (1 = upright, 2-8 = needs a flip and/or rotation to display)."""
    try:
        o = int(im.getexif().get(EXIF_ORIENTATION, 1))
    except Exception:  # noqa: BLE001 - malformed EXIF: treat as upright, like most viewers
        return 1
    return o if 1 <= o <= 8 else 1


def image_info(path: Path) -> dict:
    """Pixel size as displayed (EXIF orientation applied: 5-8 swap width and height), mode, ICC description."""
    with Image.open(path) as im:
        icc = im.info.get("icc_profile")
        desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc))).strip() if icc else None
        o = orientation(im)
        w, h = (im.height, im.width) if o >= 5 else (im.width, im.height)
        return {"width": w, "height": h, "mode": im.mode, "icc": desc, "orientation": o}


UPRIGHT_ALGO = "v1"


def upright(src: Path, dest_dir: Path, quality: int = 95, log=None) -> Path:
    """A copy of an EXIF-rotated JPEG with the orientation applied to the pixels and the tag removed,
    keeping its ICC profile; JPEG-encoded once at `quality`. Used on PDF/X-4, where WeasyPrint would
    otherwise rotate and re-encode the image itself at Pillow's default quality. Cached by content."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha1(src.read_bytes())
    h.update(f"{UPRIGHT_ALGO}:{quality}".encode())
    out = dest_dir / f"{src.stem}.{h.hexdigest()[:16]}.upright.jpg"
    if out.exists():
        return out
    with Image.open(src) as im:
        im.load()
        icc = im.info.get("icc_profile")
        o = orientation(im)
        fixed = ImageOps.exif_transpose(im)
        if fixed.mode not in ("RGB", "L", "CMYK"):
            fixed = fixed.convert("RGB")
        tmp = out.with_name(out.name + ".part")
        kw = {"icc_profile": icc} if icc else {}
        fixed.save(tmp, format="JPEG", quality=quality, subsampling=0, **kw)
        tmp.replace(out)
    if log:
        log(f"upright {src.name} (EXIF orientation {o}) -> {out.name}")
    return out


CMYK_ALGO = "v4:exif:lanczos:bpc:noicc"   # bump when the conversion itself changes, so old cache entries are not reused


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
    `None` or the source size keeps every pixel. Never upsamples. EXIF orientation is applied to the pixels
    first, so sizes are as displayed (`image_info`) and the CMYK file needs no tag. Cached by `cmyk_key`.
    Returns the file path.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    info = image_info(src)
    src_size = (info["width"], info["height"])
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
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        if size is not None:
            im = im.resize(size, Image.Resampling.LANCZOS, reducing_gap=3.0)
        prof = ImageCms.ImageCmsProfile(str(profile))
        tr = ImageCms.buildTransform(inp, prof, "RGB", "CMYK", renderingIntent=INTENTS[intent],
                                     flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
        cmyk = ImageCms.applyTransform(im, tr)
        tmp = out.with_name(out.name + ".part")
        # no embedded ICC: the image is DeviceCMYK in the PDF and the output intent already carries the
        # profile; embedding it would add the whole profile (often ~0.5-2 MB) to every image
        cmyk.save(tmp, format="JPEG", quality=quality, subsampling=0, dpi=(300, 300))
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
