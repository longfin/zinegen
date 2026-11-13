"""Spec -> HTML -> PDF/X via WeasyPrint, plus pikepdf post-processing and a JSON report."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import jinja2

from . import color
from . import layout as _layout
from .issues import Issue
from .spec import Book

STANDARDS = {"pdf/x-1a": "pdf/x-1a", "pdf/x-4": "pdf/x-4"}


def _setup_dyld():
    """WeasyPrint needs pango/cairo; on macOS with Homebrew the dylibs are not on the default path."""
    if os.uname().sysname == "Darwin" and "DYLD_FALLBACK_LIBRARY_PATH" not in os.environ:
        for prefix in ("/opt/homebrew/lib", "/usr/local/lib"):
            if Path(prefix, "libpango-1.0.dylib").exists():
                os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = prefix
                break


def font_faces(families: list[str]) -> list[dict]:
    """Resolve families to font files with fontconfig so that fonts outside pango's search path still embed."""
    faces = []
    for fam in dict.fromkeys(families):
        for style, weight in (("Regular", 400), ("Bold", 700)):
            try:
                out = subprocess.run(["fc-list", f"{fam}:style={style}", "file"], capture_output=True, text=True, timeout=10).stdout
            except (OSError, subprocess.SubprocessError):
                out = ""
            files = [ln.strip().rstrip(":") for ln in out.splitlines() if ln.strip()]
            if files:
                faces.append({"family": fam, "weight": weight, "file": Path(files[0]).as_uri()})
    return faces


def source_files(book: Book) -> dict[str, Path]:
    files = {}
    for pid in book.photo_ids():
        f = book.cache_dir / f"{pid}.jpg"
        if not f.exists():
            raise FileNotFoundError(f"missing cached photo {pid}; run `zinegen fetch` first")
        files[pid] = f.resolve()
    return files


def resolve_icc(book: Book) -> Path | None:
    """The output-intent profile, or None when `output.icc` is unset on PDF/X-4.

    A profile that is named but cannot be found is an error on both standards: it is almost always a typo,
    and the only PDF we could write without it has no output intent, which no printer accepts as PDF/X.
    """
    name = book.output.get("icc")
    std = book.output["standard"]
    if name:
        icc = color.find_profile(name)
        if icc is None:
            where = ", ".join(str(d) for d in color.ADOBE_PROFILE_DIRS)
            raise ValueError(f"output.icc {name!r} not found: it is not an existing file, and no {name}, {name}.icc "
                             f"or {name}.icm exists in {where}. Use a full path, or a name that `zinegen doctor` lists.")
    else:
        icc = None
    if std == "pdf/x-1a":
        if not icc:
            raise ValueError("pdf/x-1a needs `output.icc` (CMYK profile path or name, e.g. JapanColor2001Coated)")
        if color.profile_components(icc) != ["c", "m", "y", "k"]:
            raise ValueError(f"pdf/x-1a needs a CMYK output profile; {icc.name} is not CMYK")
    return icc


def prepare_images(book: Book, log=None) -> tuple[dict[str, Path], dict[str, tuple[int, int]], dict]:
    """Return (photo id -> file to embed, photo id -> pixel size, colour info)."""
    std = book.output["standard"]
    srcs = source_files(book)
    info = {"standard": std}
    icc = resolve_icc(book)
    if std == "pdf/x-1a":
        files = {pid: color.to_cmyk(f, icc, book.cache_dir.resolve() / "cmyk", book.output.get("intent", "perceptual"),
                                    int(book.output.get("jpeg_quality", 95)), log) for pid, f in srcs.items()}
    else:
        files = dict(srcs)
    sizes = {}
    for pid, f in srcs.items():
        i = color.image_info(f)
        sizes[pid] = (i["width"], i["height"])
    info["icc"] = str(icc) if icc else None
    info["icc_description"] = color.profile_description(icc) if icc else None
    info["icc_components"] = color.profile_components(icc) if icc else None
    return files, sizes, info


def render(book: Book, log=None, marks: bool | None = None) -> dict:
    _setup_dyld()
    import weasyprint  # imported late so `zinegen doctor` can explain a broken install

    files, sizes, cinfo = prepare_images(book, log)
    pages = _layout.layout(book, sizes)
    rep = _layout.report(book, pages)
    rep.update(cinfo)
    if not cinfo["icc"]:
        rep["issues"].append(Issue("NO_OUTPUT_PROFILE", "output.icc is not set, so the PDF has no output intent and "
                                   "is not valid PDF/X-4; fine for proofs, not for print. Set output.icc to the "
                                   "printer's profile.").to_dict())
    env = jinja2.Environment(loader=jinja2.PackageLoader("zinegen", "templates"), autoescape=True)
    tpl = env.get_template("book.html.j2")
    icc_uri = Path(cinfo["icc"]).resolve().as_uri() if cinfo["icc"] else None
    html = tpl.render(book=book, pages=pages, files={k: Path(v).resolve().as_uri() for k, v in files.items()},
                      W=book.trim_w, H=book.trim_h, B=book.bleed, fonts=book.fonts, type=book.type,
                      icc=icc_uri, icc_components=", ".join(cinfo["icc_components"] or []),
                      marks=book.marks if marks is None else marks,
                      font_faces=font_faces([book.fonts["body"], book.fonts["caption"], book.fonts["title"]]))
    book.out_dir.mkdir(parents=True, exist_ok=True)
    html_path = book.out_dir / "interior.html"
    html_path.write_text(html)
    pdf_path = book.out_dir / "interior.pdf"
    std = STANDARDS[book.output["standard"]]
    doc = weasyprint.HTML(string=html, base_url=str(html_path))
    # jpeg_quality=None and dpi=None keep WeasyPrint's JPEG passthrough: any quality re-encodes every JPEG,
    # and dpi thumbnails (and re-encodes) images above it.
    doc.write_pdf(pdf_path, pdf_variant=std, output_intent="--press" if icc_uri else None,
                  jpeg_quality=None, dpi=None, optimize_images=False)
    postprocess(pdf_path, book)
    rep["pdf"] = str(pdf_path)
    rep["html"] = str(html_path)
    (book.out_dir / "report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False))
    if log:
        log(f"wrote {pdf_path} ({pdf_path.stat().st_size // 1024} KB), {rep['pages']} pages, {len(rep['issues'])} issues")
    return rep


def postprocess(pdf_path: Path, book: Book):
    """Drop colour-space resources WeasyPrint declares but never uses (Lab, ICCBased) on PDF/X-1a,
    set document info, and tag untagged RGB images with their embedded ICC profile on PDF/X-4."""
    import pikepdf

    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        pdf.docinfo["/Title"] = book.title
        pdf.docinfo["/Creator"] = "zinegen"
        strict = book.output["standard"] == "pdf/x-1a"
        if not strict:
            _tag_rgb_images(pdf)
        for page in pdf.pages:
            cs = page.Resources.get("/ColorSpace")
            if cs is None:
                continue
            content = page.Contents
            data = content.read_bytes() if not isinstance(content, pikepdf.Array) else b"".join(c.read_bytes() for c in content)
            used = set(re.findall(rb"/(\S+)\s+(?:cs|CS)\b", data))
            for name in list(cs.keys()):
                if name.lstrip("/").encode() not in used:
                    if strict or str(name).startswith("/lab-"):
                        del cs[name]
        pdf.save(pdf_path)


def _tag_rgb_images(pdf):
    """PDF/X-4 forbids untagged DeviceRGB. WeasyPrint passes JPEGs through as DeviceRGB, so attach the
    ICC profile embedded in each JPEG (Display P3 for Lightroom renditions) as an ICCBased colour space."""
    import io

    import pikepdf
    from PIL import Image

    profiles: dict[bytes, pikepdf.Object] = {}
    seen = set()
    for page in pdf.pages:
        for _, x in page.images.items():
            if x.objgen in seen:
                continue
            seen.add(x.objgen)
            if str(x.get("/ColorSpace")) != "/DeviceRGB" or str(x.get("/Filter")) != "/DCTDecode":
                continue
            try:
                icc = Image.open(io.BytesIO(x.read_raw_bytes())).info.get("icc_profile")
            except Exception:  # noqa: BLE001
                icc = None
            if not icc:
                icc = (Path(__file__).parent / "templates" / "sRGB2014.icc").read_bytes()
            if icc not in profiles:
                stream = pikepdf.Stream(pdf, icc)
                stream["/N"] = 3
                stream["/Alternate"] = pikepdf.Name("/DeviceRGB")
                profiles[icc] = pdf.make_indirect(stream)
            x["/ColorSpace"] = pikepdf.Array([pikepdf.Name("/ICCBased"), profiles[icc]])
