"""Spec -> HTML -> PDF/X via WeasyPrint, plus pikepdf post-processing and a JSON report."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path

import jinja2

from . import color
from . import layout as _layout
from .issues import Issue
from .spec import DEFAULT_MAX_DPI, Book

STANDARDS = {"pdf/x-1a": "pdf/x-1a", "pdf/x-4": "pdf/x-4"}
# A photo whose most demanding placement is at most this factor above output.max_dpi is converted without
# resampling: a Lanczos pass for a few percent of bytes is not worth the softening.
RESAMPLE_TOLERANCE = 1.1
# Lightroom rendition sizes below fullsize (long edge, px), for the DPI_ABOVE_NEED hint.
SOURCE_SIZES = (2560, 2048, 1280, 640)
PRINT_DPI = 300             # LOW_DPI threshold in layout.report


class RenderError(RuntimeError):
    """The render produced a PDF that cannot be trusted (it has been removed)."""


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
    files, absent = {}, []
    for pid in book.photo_ids():
        f = book.cache_dir / f"{pid}.jpg"
        if f.exists():
            files[pid] = f.resolve()
        else:
            absent.append(pid)
    if absent:
        # ids the last fetch could not find anywhere: fetching again cannot help, book.yaml must change
        gone = [pid for pid in absent if pid in book.missing_ids()]
        if gone:
            raise FileNotFoundError(f"{len(gone)} photo id(s) in book.yaml are not in the album or the Lightroom "
                                    f"catalog (the last fetch reported them missing): {', '.join(gone)}. Replace "
                                    "them with ids from the album (photos.json) or remove those pages.")
        raise FileNotFoundError(f"{len(absent)} photo(s) not downloaded yet ({', '.join(absent)}); "
                                "run `zinegen fetch` first")
    return files


def resolve_icc(book: Book) -> Path | None:
    """The output-intent profile, or None when `output.icc` is unset on PDF/X-4.

    A profile that is named but cannot be found is an error on both standards: it is almost always a typo,
    and the only PDF we could write without it has no output intent, which no printer accepts as PDF/X.
    """
    name = book.output.get("icc")
    std = book.output["standard"]
    if name:
        icc = color.find_profile(name, base=book.root)
        if icc is None:
            where = ", ".join(str(d) for d in color.ADOBE_PROFILE_DIRS)
            raise ValueError(f"output.icc {name!r} not found: it is not an existing file (relative paths are "
                             f"resolved against the book directory {book.root}), and no "
                             f"{color.profile_candidates(name)} exists in {where}. Use a path, or a name that "
                             "`zinegen doctor` lists.")
        color.check_profile(icc, color.profile_label("output.icc", name, icc))
    else:
        icc = None
    if std == "pdf/x-1a":
        if not icc:
            raise ValueError("pdf/x-1a needs `output.icc` (CMYK profile path or name, e.g. JapanColor2001Coated)")
        if color.profile_components(icc) != ["c", "m", "y", "k"]:
            raise ValueError(f"pdf/x-1a needs a CMYK output profile; {icc.name} is not CMYK")
    return icc


def max_dpi(book: Book) -> int | None:
    """`output.max_dpi`: target effective resolution for PDF/X-1a images; None disables downsampling."""
    v = book.output.get("max_dpi", DEFAULT_MAX_DPI)
    return int(v) if v else None


def target_sizes(pages: list[_layout.PageLayout], sizes: dict[str, tuple[int, int]],
                 dpi: int | None) -> dict[str, tuple[int, int]]:
    """Pixel size to embed per photo so that its most demanding placement still gets `dpi`.

    `pages` must be laid out with the source `sizes`. A cover placement's `w` is the full scaled image (the
    cropped part included), so `px / (w / 25.4)` is the effective resolution of every visible pixel. A photo
    used more than once is sized for its largest placement. Never upsamples; rounds up so the target holds.
    Photos at most RESAMPLE_TOLERANCE above the target keep their source size.
    """
    if not dpi:
        return dict(sizes)
    need: dict[str, float] = {}
    for pl in pages:
        for im in pl.images:
            scale = dpi * (im.w / 25.4) / sizes[im.photo][0]
            need[im.photo] = max(need.get(im.photo, 0.0), scale)
    out = {}
    for pid, (w, h) in sizes.items():
        s = need.get(pid, 1.0)
        out[pid] = (w, h) if s * RESAMPLE_TOLERANCE >= 1 else (min(w, math.ceil(w * s - 1e-9)), min(h, math.ceil(h * s - 1e-9)))
    return out


def placed(pages: list[_layout.PageLayout]) -> list[str]:
    """Photo ids that have at least one placement (a NO_ROOM page places nothing), in order."""
    return list(dict.fromkeys(im.photo for pl in pages for im in pl.images))


def prepare_images(book: Book, pages: list[_layout.PageLayout], srcs: dict[str, Path],
                   src_info: dict[str, dict], icc: Path | None,
                   log=None) -> tuple[dict[str, Path], dict[str, tuple[int, int]], dict, list[Issue]]:
    """Return (photo id -> file to embed, photo id -> pixel size of that file, colour info, issues), for the
    photos that are placed; unplaced photos are neither converted nor embedded.

    PDF/X-1a: one conversion per photo from the source pixels (EXIF orientation applied, resampled to
    `target_sizes`, then P3 -> CMYK, then one JPEG encode). PDF/X-4: the source files, byte for byte, except a
    photo with an EXIF orientation, which is rotated and encoded once here (ORIENTATION_REENCODED) instead of
    being rotated and re-encoded by WeasyPrint at Pillow's default quality.
    """
    std = book.output["standard"]
    info = {"standard": std}
    issues: list[Issue] = []
    ids = placed(pages)
    src_sizes = {pid: (src_info[pid]["width"], src_info[pid]["height"]) for pid in ids}
    quality = int(book.output.get("jpeg_quality", 95))
    if std == "pdf/x-1a":
        targets = target_sizes(pages, src_sizes, max_dpi(book))
        cmyk_dir = book.cache_dir.resolve() / "cmyk"
        files = {pid: color.to_cmyk(srcs[pid], icc, cmyk_dir, book.output.get("intent", "perceptual"),
                                    quality, log, size=targets[pid]) for pid in ids}
        _prune(cmyk_dir, set(files.values()), "cmyk")
        sizes = {pid: tuple(color.image_info(f)[k] for k in ("width", "height")) for pid, f in files.items()}
    else:
        files, sizes = {pid: srcs[pid] for pid in ids}, dict(src_sizes)
        upright_dir = book.cache_dir.resolve() / "upright"
        for pid in ids:
            o = src_info[pid]["orientation"]
            if o != 1:
                files[pid] = color.upright(srcs[pid], upright_dir, quality, log)
                issues.append(Issue("ORIENTATION_REENCODED", f"EXIF orientation {o}: rotated and JPEG-encoded once "
                                    f"(quality {quality}) to embed it upright, so this photo is not the source bytes. "
                                    "Export it upright from Lightroom to avoid the extra generation.", photo=pid))
        if upright_dir.exists():
            _prune(upright_dir, set(files.values()), "upright")
    info["icc"] = str(icc) if icc else None
    info["icc_description"] = color.profile_description(icc) if icc else None
    info["icc_components"] = color.profile_components(icc) if icc else None
    return files, sizes, info, issues


def _prune(directory: Path, keep: set[Path], kind: str):
    """Drop derived images (`*.<kind>.jpg` and sidecars) this render does not use (earlier layouts, sizes or
    profiles, unplaced photos), so the cache does not grow with every iteration."""
    for f in directory.glob(f"*.{kind}.*"):
        if f.with_suffix(".jpg") not in keep:
            f.unlink(missing_ok=True)


def render(book: Book, log=None, marks: bool | None = None) -> dict:
    _setup_dyld()
    import weasyprint  # imported late so `zinegen doctor` can explain a broken install

    icc = resolve_icc(book)
    srcs = source_files(book)
    src_info = {pid: color.image_info(f) for pid, f in srcs.items()}
    src_sizes = {pid: (i["width"], i["height"]) for pid, i in src_info.items()}   # as displayed (EXIF applied)
    # Geometry comes from the source pixels, so downsampling can never move anything on the page.
    pages = _layout.layout(book, src_sizes)
    files, sizes, cinfo, img_issues = prepare_images(book, pages, srcs, src_info, icc, log)
    for pl in pages:                       # report the resolution of what is actually embedded
        for im in pl.images:
            im.px = sizes[im.photo]
    rep = _layout.report(book, pages)
    rep.update(cinfo)
    rep["max_dpi"] = max_dpi(book) if book.output["standard"] == "pdf/x-1a" else None
    for item in rep["images"]:
        item["source_px"] = list(src_sizes[item["photo"]])
    rep["issues"] += [i.to_dict() for i in img_issues + _render_issues(book, pages, icc)]
    env = jinja2.Environment(loader=jinja2.PackageLoader("zinegen", "templates"), autoescape=True)
    env.filters["keep_all"] = keep_all
    tpl = env.get_template("book.html.j2")
    icc_uri = Path(cinfo["icc"]).resolve().as_uri() if cinfo["icc"] else None
    kinds = list(_layout.LINE_HEIGHT)
    html = tpl.render(book=book, pages=pages, files={k: Path(v).resolve().as_uri() for k, v in files.items()},
                      W=book.trim_w, H=book.trim_h, B=book.bleed, fonts=book.fonts,
                      pt={k: _layout.font_pt(book, k) for k in kinds}, lh=_layout.LINE_HEIGHT,
                      icc=icc_uri, icc_components=", ".join(cinfo["icc_components"] or []),
                      font_faces=font_faces([book.fonts["body"], book.fonts["caption"], book.fonts["title"]]))
    book.out_dir.mkdir(parents=True, exist_ok=True)
    html_path = book.out_dir / "interior.html"
    html_path.write_text(html)
    pdf_path = book.out_dir / "interior.pdf"
    std = STANDARDS[book.output["standard"]]
    doc = weasyprint.HTML(string=html, base_url=str(html_path))
    # jpeg_quality=None and dpi=None keep WeasyPrint's JPEG passthrough: any quality re-encodes every JPEG,
    # and dpi thumbnails (and re-encodes) images above it. Resolution is decided once, in prepare_images.
    # Laid out first (same options) so that clipped text can be reported from WeasyPrint's line boxes.
    opts = dict(pdf_variant=std, output_intent="--press" if icc_uri else None,
                jpeg_quality=None, dpi=None, optimize_images=False)
    document = doc.render(**opts)
    rep["issues"] += [i.to_dict() for i in text_overflow(document)]
    document.write_pdf(pdf_path, pdf_variant=std, output_intent="--press" if icc_uri else None,
                       jpeg_quality=None, dpi=None, optimize_images=False)
    _verify_images(pdf_path, {im.photo: files[im.photo] for pl in pages for im in pl.images})
    postprocess(pdf_path, book, marks=book.marks if marks is None else marks, icc=icc)
    rep["pdf"] = str(pdf_path)
    rep["html"] = str(html_path)
    (book.out_dir / "report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False))
    if log:
        log(f"wrote {pdf_path} ({pdf_path.stat().st_size // 1024} KB), {rep['pages']} pages, {len(rep['issues'])} issues")
    return rep


WORD_JOINER = "⁠"
_HANGUL = "가-힣ᄀ-ᇿ㄰-㆏"
# Between two letters/digits of one word, at least one of them Hangul: where `word-break: keep-all` forbids a
# line break that UAX #14 (and so Pango) allows between Hangul syllables.
_KEEP_ALL_RE = re.compile(rf"(?<=[{_HANGUL}])(?=\w)|(?<=\w)(?=[{_HANGUL}])")


def keep_all(text: str) -> str:
    """`word-break: keep-all` for Korean, which WeasyPrint does not implement: join the letters of each word with
    U+2060 WORD JOINER (zero width, not printed) so lines break only at spaces and punctuation. A word wider than
    its box is still broken by `overflow-wrap: anywhere` in the template."""
    return _KEEP_ALL_RE.sub(WORD_JOINER, str(text))


def text_overflow(document) -> list[Issue]:
    """TEXT_CLIPPED for every text box whose lines do not all fit. Boxes are cut to whole lines in layout.py,
    so the clip falls between lines; this says which text lost lines. Reads WeasyPrint's laid-out boxes:
    each `.t` block (data-page, data-kind) and its line boxes."""
    from weasyprint.formatting_structure import boxes

    issues, seen = [], set()
    for page in document.pages:
        for box in page._page_box.descendants(placeholders=True):     # .t boxes are absolutely positioned
            el = getattr(box, "element", None)
            if not isinstance(box, boxes.BlockBox) or el is None or el.get("data-kind") is None or id(el) in seen:
                continue
            seen.add(id(el))
            lines = [b for b in box.descendants() if isinstance(b, boxes.LineBox)]
            bottom = box.content_box_y() + box.height
            fit = sum(1 for lb in lines if lb.position_y + lb.height <= bottom + 0.01)
            if fit < len(lines):
                kind = el.get("data-kind")
                cut = " ".join("".join(t.text for t in lb.descendants() if isinstance(t, boxes.TextBox)).strip()
                               for lb in lines[fit:]).replace(WORD_JOINER, "")
                issues.append(Issue("TEXT_CLIPPED", f"{kind} needs {len(lines)} lines, its box holds {fit}: "
                                    f"{cut[:40]!r}{'…' if len(cut) > 40 else ''} is not printed. Shorten the text"
                                    + (" (a caption box holds 2 lines)" if kind == "caption" else ""),
                                    page=int(el.get("data-page"))))
    return issues


def _render_issues(book: Book, pages: list[_layout.PageLayout], icc: Path | None) -> list[Issue]:
    issues = []
    if icc is None:
        issues.append(Issue("NO_OUTPUT_PROFILE", "output.icc is not set, so the PDF has no output intent and is not "
                            "valid PDF/X-4; fine for proofs, not for print. Set output.icc to the printer's profile."))
    elif (dc := color.profile_class(icc)) != "prtr":
        cls = {"mntr": "display", "scnr": "input", "spac": "colour space", "link": "device link",
               "abst": "abstract"}.get(dc, dc)
        issues.append(Issue("DISPLAY_OUTPUT_PROFILE", f"output.icc {icc.name} is a {cls} profile, not a printer "
                            "(output) profile: the output intent describes a screen, not the press. Fine for proofs "
                            "and screen PDFs; for print set output.icc to the printer's profile."))
    if book.output["standard"] == "pdf/x-4":
        issues += _dpi_above_need(book, pages)
    return issues


def _dpi_above_need(book: Book, pages: list[_layout.PageLayout]) -> list[Issue]:
    """One info issue when X-4 placements are far above what print needs (X-4 never resamples).

    Suggests a smaller `source.size` only when every placement would still reach PRINT_DPI with it, so the
    advice can never lead into LOW_DPI."""
    target = max_dpi(book) or DEFAULT_MAX_DPI
    placements: dict[tuple[int, str], _layout.ImageEl] = {}
    for pl in pages:
        for im in pl.images:            # a spread photo is one placement (two page entries)
            placements.setdefault((im.spread if im.spread is not None else pl.index, im.photo), im)
    high = [im for im in placements.values() if im.dpi > target * 1.5]
    if not high:
        return []
    msg = (f"{len(high)} of {len(placements)} placements are above {round(target * 1.5)} dpi "
           f"(max {round(max(im.dpi for im in high))}); PDF/X-4 embeds the source JPEGs unchanged, so the file is "
           "larger than print needs.")
    current = str(book.source.get("size", "fullsize"))
    current_px = float("inf") if current == "fullsize" else int(current)

    def dpi_at(im, long_edge):
        return im.dpi * min(1.0, long_edge / max(im.px))

    fits = [s for s in SOURCE_SIZES
            if s < current_px and all(dpi_at(im, s) >= PRINT_DPI for im in placements.values())]
    if fits:
        s = min(fits)
        low = min(dpi_at(im, s) for im in placements.values())
        msg += f" source.size: {s} would keep every placement at {int(low)} dpi or more and shrink it."
    return [Issue("DPI_ABOVE_NEED", msg)]


def _verify_images(pdf_path: Path, expected: dict[str, Path]):
    """Fail when a placed photo is not in the PDF as prepared. WeasyPrint only logs a warning for an image it
    cannot load (e.g. a cache file removed by a concurrent render), which would silently drop a photo. Every
    prepared file is upright and passed through byte for byte, so each must appear as an image stream."""
    import hashlib

    import pikepdf

    with pikepdf.open(pdf_path) as pdf:
        embedded = {hashlib.sha1(x.read_raw_bytes()).hexdigest()
                    for page in pdf.pages for x in page.images.values()}
    missing = []
    for pid, f in expected.items():
        try:
            ok = hashlib.sha1(Path(f).read_bytes()).hexdigest() in embedded
        except OSError:
            ok = False
        if not ok:
            missing.append(pid)
    if missing:
        pdf_path.unlink(missing_ok=True)
        raise RenderError(f"{len(missing)} placed photo(s) not embedded as prepared ({', '.join(sorted(missing))}): "
                          "the PDF has no image stream identical to the file prepared for them, so it was removed. "
                          "WeasyPrint logs why it skipped or altered an image; a cache file removed or rewritten "
                          "during the render (e.g. by another render of this book) is one possible cause.")


def postprocess(pdf_path: Path, book: Book, marks: bool = False, icc: Path | None = None):
    """Drop colour-space resources WeasyPrint declares but never uses (Lab, ICCBased) on PDF/X-1a,
    set document info, tag untagged RGB images with their embedded ICC profile on PDF/X-4, and draw the
    crop marks (`add_marks`)."""
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
        if marks:
            cmyk = icc is not None and color.profile_components(icc) == ["c", "m", "y", "k"]
            add_marks(pdf, _layout.crop_marks(book), book.trim_h, cmyk)
        pdf.save(pdf_path)


MARK_LINE_PT = 0.25


def add_marks(pdf, marks: _layout.Marks, trim_h: float, cmyk: bool):
    """Draw crop marks and registration targets outside the bleed and grow the MediaBox to hold them.

    WeasyPrint's own `marks: crop cross` cannot be used for print: it draws them as an SVG with
    `fill="transparent" stroke="black"`, i.e. DeviceRGB strokes inside a transparency group with a
    `ca 0` ExtGState, and inside the bleed (its MediaBox is the BleedBox). Here they are one Form XObject
    with no group, painted in the registration colour (Separation /All, which prints on every plate), with
    a DeviceCMYK alternate for a CMYK output intent and DeviceGray otherwise, so it is valid on PDF/X-1a and
    on PDF/X-4 with any output intent. The page content is wrapped in q/Q first so its graphics state
    (WeasyPrint leaves a flipped CTM in place) cannot move the marks.
    """
    import pikepdf

    pt = 72 / 25.4
    if cmyk:
        alt, c0, c1 = pikepdf.Name.DeviceCMYK, [0, 0, 0, 0], [1, 1, 1, 1]
    else:
        alt, c0, c1 = pikepdf.Name.DeviceGray, [1], [0]
    tint = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=c0, C1=c1, N=1)
    reg = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/All"), alt, tint])

    def p(x, y):        # page mm (top-left origin, y down) -> PDF points relative to the TrimBox origin
        return f"{x * pt:.3f} {(trim_h - y) * pt:.3f}"

    ops = [f"/Reg CS 1 SCN {MARK_LINE_PT} w 0 J [] 0 d"]
    for x0, y0, x1, y1 in marks.lines:
        ops.append(f"{p(x0, y0)} m {p(x1, y1)} l S")
    k = 0.5523                                           # Bezier circle constant
    for cx, cy, r in marks.targets:
        ops.append(f"{p(cx + r, cy)} m "
                   f"{p(cx + r, cy - k * r)} {p(cx + k * r, cy - r)} {p(cx, cy - r)} c "
                   f"{p(cx - k * r, cy - r)} {p(cx - r, cy - k * r)} {p(cx - r, cy)} c "
                   f"{p(cx - r, cy + k * r)} {p(cx - k * r, cy + r)} {p(cx, cy + r)} c "
                   f"{p(cx + k * r, cy + r)} {p(cx + r, cy + k * r)} {p(cx + r, cy)} c S")
        h = r + 1.0
        ops.append(f"{p(cx - h, cy)} m {p(cx + h, cy)} l S {p(cx, cy - h)} m {p(cx, cy + h)} l S")
    s = marks.slug * pt
    form = pikepdf.Stream(pdf, "\n".join(ops).encode())     # every page has the same trim size: one form
    form["/Type"] = pikepdf.Name.XObject
    form["/Subtype"] = pikepdf.Name.Form
    form["/Resources"] = pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(Reg=reg))
    form = pdf.make_indirect(form)
    for page in pdf.pages:
        tb = [float(v) for v in page.obj["/TrimBox"]]
        form["/BBox"] = pikepdf.Array([round(v, 3) for v in (-s, -s, tb[2] - tb[0] + s, tb[3] - tb[1] + s)])
        page.obj["/MediaBox"] = pikepdf.Array([round(v, 3) for v in (tb[0] - s, tb[1] - s, tb[2] + s, tb[3] + s)])
        if "/CropBox" in page.obj:
            del page.obj["/CropBox"]
        if "/Resources" not in page.obj:
            page.obj["/Resources"] = pikepdf.Dictionary()
        if "/XObject" not in page.obj.Resources:
            page.obj.Resources["/XObject"] = pikepdf.Dictionary()
        page.obj.Resources.XObject["/ZgMarks"] = form
        page.contents_add(pikepdf.Stream(pdf, b"q\n"), prepend=True)
        page.contents_add(pikepdf.Stream(pdf, f"\nQ\nq 1 0 0 1 {tb[0]:.3f} {tb[1]:.3f} cm /ZgMarks Do Q\n".encode()))


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
