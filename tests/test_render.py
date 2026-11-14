"""End-to-end render without Lightroom: synthetic sRGB JPEGs in the cache, PDF/X-4 with an RGB output intent.
Skipped when WeasyPrint's native libraries (pango) are missing."""
from __future__ import annotations

import shutil
from importlib.resources import files
from pathlib import Path

import pytest
import yaml
from PIL import Image

from zinegen import check, render, spec
from zinegen.render import _setup_dyld

A = "00000000000000000000000000000001"
B = "00000000000000000000000000000002"

_setup_dyld()
weasyprint = pytest.importorskip("weasyprint")


def synthetic_jpeg(path: Path, w: int, h: int, orientation: int | None = None):
    """Like a Lightroom rendition: ICC-tagged and with an EXIF block (camera, date; Orientation only when
    given). WeasyPrint used to re-encode any JPEG with an EXIF block, so EXIF-free test files hid that."""
    im = Image.linear_gradient("L").resize((w, h)).convert("RGB")
    icc = (files("zinegen") / "templates" / "sRGB2014.icc").read_bytes()
    exif = Image.Exif()
    exif[0x010F] = "zinegen-test"            # Make
    exif[0x0110] = "synthetic"               # Model
    exif[0x0132] = "2026:11:14 10:00:00"     # DateTime
    if orientation is not None:
        exif[0x0112] = orientation
    im.save(path, quality=90, icc_profile=icc, exif=exif)


@pytest.fixture
def book_dir(tmp_path: Path) -> Path:
    (tmp_path / "cache").mkdir()
    synthetic_jpeg(tmp_path / "cache" / f"{A}.jpg", 3600, 2400)
    synthetic_jpeg(tmp_path / "cache" / f"{B}.jpg", 1600, 2400)
    doc = {
        "version": 1, "title": "Synthetic", "trim": {"preset": "A5"}, "binding": "perfect",
        "fonts": {"body": "DejaVu Serif", "caption": "DejaVu Sans", "title": "DejaVu Serif"},
        "output": {"standard": "pdf/x-4", "icc": str(files("zinegen") / "templates" / "sRGB2014.icc")},
        "pages": [
            {"template": "title", "title": "Synthetic", "subtitle": "test"},
            {"template": "full-bleed", "photo": A, "focal": [0.3, 0.3]},
            {"template": "margin", "photo": B, "caption": "A caption"},
            {"template": "spread-full", "photo": A},
            {"template": "pair", "photos": [A, B]},
            {"template": "colophon", "text": "colophon"},
        ],
    }
    (tmp_path / "book.yaml").write_text(yaml.safe_dump(doc))
    return tmp_path


def test_render_and_check_pdfx4(book_dir: Path):
    book = spec.load(book_dir / "book.yaml")
    rep = render.render(book)
    assert rep["version"] == 1 and rep["pages"] == 8
    pdf = book_dir / "out" / "interior.pdf"
    assert pdf.exists() and pdf.stat().st_size > 10_000
    res = check.check(book)
    codes = {i["code"] for i in res["issues"]}
    assert res["ok"], res["issues"]
    assert "LOW_DPI" in codes            # 3600 px across a 324 mm cover placement is < 300 dpi
    assert "GUTTER_CROSSING" in codes
    assert "DISPLAY_OUTPUT_PROFILE" in codes   # sRGB2014 is a display profile: a warning, the fallback still works
    assert "TEXT_CLIPPED" not in codes
    assert res["info"]["pdfx_version"] == "PDF/X-4"
    assert "sRGB" in res["info"]["output_intent"]

    import pikepdf
    with pikepdf.open(pdf) as p:
        spaces = set()
        for page in p.pages:
            for _, x in page.images.items():
                cs = x.get("/ColorSpace")
                spaces.add(str(cs[0]) if isinstance(cs, pikepdf.Array) else str(cs))
        assert spaces == {"/ICCBased"}
        assert [round(float(v)) for v in p.pages[0].TrimBox] == [0, 0, 420, 595]


def test_pdfx4_embeds_source_bytes_unchanged(book_dir: Path):
    """No second lossy generation: WeasyPrint must pass the cached JPEGs through untouched, which also
    keeps their embedded ICC profile for the ICCBased tagging."""
    import hashlib

    import pikepdf
    book = spec.load(book_dir / "book.yaml")
    for f in (book_dir / "cache").glob("*.jpg"):
        with Image.open(f) as im:
            assert "exif" in im.info and 0x0112 not in im.getexif()      # EXIF block, no Orientation
    rep = render.render(book)
    assert not any(i["code"] == "ORIENTATION_REENCODED" for i in rep["issues"])
    src = {hashlib.sha1(f.read_bytes()).hexdigest() for f in (book_dir / "cache").glob("*.jpg")}
    with pikepdf.open(book_dir / "out" / "interior.pdf") as p:
        embedded = {hashlib.sha1(x.read_raw_bytes()).hexdigest() for page in p.pages for _, x in page.images.items()}
    assert embedded == src
    assert rep["max_dpi"] is None
    assert all(i["px"] == i["source_px"] for i in rep["images"])


def test_pdfx1a_downsampled_once(book_dir: Path, cmyk_icc: Path):
    import hashlib
    import io

    import pikepdf
    from PIL import Image
    _set_output(book_dir, standard="pdf/x-1a", icc=str(cmyk_icc), max_dpi=200)
    cmyk_dir = book_dir / "cache" / "cmyk"
    cmyk_dir.mkdir()
    stale = [cmyk_dir / f"{A}.0123456789abcdef.cmyk.jpg", cmyk_dir / f"{A}.0123456789abcdef.cmyk.json"]
    for f in stale:
        f.write_bytes(b"stale")
    book = spec.load(book_dir / "book.yaml")
    rep = render.render(book)
    res = check.check(book)
    assert res["ok"], res["issues"]
    assert not any(i["code"] == "DISPLAY_OUTPUT_PROFILE" for i in res["issues"])   # a printer-class profile
    assert res["info"]["pdfx_version"] == "PDF/X-1a:2003"
    assert not any(f.exists() for f in stale)                         # pruned
    assert (book_dir / "cache" / f"{A}.jpg").exists()                 # sources untouched
    kept = sorted(cmyk_dir.glob("*.cmyk.jpg"))
    assert len(kept) == 2 and all(f.with_suffix(".json").exists() for f in kept)
    cmyk = {hashlib.sha1(f.read_bytes()).hexdigest() for f in kept}
    with pikepdf.open(book_dir / "out" / "interior.pdf") as p:
        raws = {x.objgen: x.read_raw_bytes() for page in p.pages for _, x in page.images.items()}
    assert {hashlib.sha1(r).hexdigest() for r in raws.values()} == cmyk  # one conversion per photo, as is
    for r in raws.values():
        with Image.open(io.BytesIO(r)) as im:
            assert im.mode == "CMYK" and "icc_profile" not in im.info  # the output intent carries the profile
    assert rep["max_dpi"] == 200
    for photo in {i["photo"] for i in rep["images"]}:
        items = [i for i in rep["images"] if i["photo"] == photo]
        # the most demanding placement lands on the target unless the source was within the tolerance
        assert min(i["dpi"] for i in items) == 200 or items[0]["px"] == items[0]["source_px"]
    assert any(i["px"][0] < i["source_px"][0] for i in rep["images"])


def test_missing_image_fails_render(book_dir: Path, monkeypatch, capsys):
    """A cache file that disappears between conversion and write_pdf (e.g. pruned by a concurrent render)
    is only a WeasyPrint warning; render must not return a PDF with a photo missing."""
    real = render.prepare_images

    def vanish(*a, **k):
        files, sizes, info, issues = real(*a, **k)
        victim = book_dir / "gone.jpg"
        victim.write_bytes(files[B].read_bytes())
        files = {**files, B: victim}
        victim.unlink()
        return files, sizes, info, issues

    monkeypatch.setattr(render, "prepare_images", vanish)
    book = spec.load(book_dir / "book.yaml")
    with pytest.raises(render.RenderError, match=f"not embedded as prepared \\({B}\\)"):
        render.render(book)
    assert not (book_dir / "out" / "interior.pdf").exists()

    # the CLI reports it like any other render failure: one error line, exit status, no traceback
    from zinegen import cli
    assert cli.main(["render", str(book_dir / "book.yaml")]) == 2
    err = capsys.readouterr().err
    assert "error: 1 placed photo(s) not embedded as prepared" in err and "Traceback" not in err


def _exif_rotated_jpeg(path: Path, w: int, h: int, orientation: int):
    synthetic_jpeg(path, w, h, orientation)


def test_orientation_1_passes_through_pdfx4(book_dir: Path):
    """Orientation=1 (upright) is the common tagged case: embedded byte for byte, no warning."""
    import hashlib

    import pikepdf
    synthetic_jpeg(book_dir / "cache" / f"{B}.jpg", 1600, 2400, orientation=1)
    book = spec.load(book_dir / "book.yaml")
    rep = render.render(book)
    assert not any(i["code"] == "ORIENTATION_REENCODED" for i in rep["issues"])
    assert not (book_dir / "cache" / "upright").exists()
    with pikepdf.open(book_dir / "out" / "interior.pdf") as p:
        embedded = {hashlib.sha1(x.read_raw_bytes()).hexdigest() for page in p.pages for _, x in page.images.items()}
    assert embedded == {hashlib.sha1(f.read_bytes()).hexdigest() for f in (book_dir / "cache").glob("*.jpg")}


def test_exif_orientation_pdfx4(book_dir: Path):
    """A JPEG with EXIF orientation 6 (stored 2400x1600, displayed 1600x2400) renders on X-4: laid out as
    displayed, embedded upright from one re-encode we control, reported, and verified."""
    import hashlib

    import pikepdf
    _exif_rotated_jpeg(book_dir / "cache" / f"{B}.jpg", 2400, 1600, 6)
    book = spec.load(book_dir / "book.yaml")
    rep = render.render(book)
    assert all(i["source_px"] == [1600, 2400] for i in rep["images"] if i["photo"] == B)
    orient = [i for i in rep["issues"] if i["code"] == "ORIENTATION_REENCODED"]
    assert [(i["photo"], i["level"]) for i in orient] == [(B, "warning")]
    upright = list((book_dir / "cache" / "upright").glob("*.upright.jpg"))
    assert len(upright) == 1
    with Image.open(upright[0]) as im:
        assert im.size == (1600, 2400) and "exif" not in im.info and im.info.get("icc_profile")
    with pikepdf.open(book_dir / "out" / "interior.pdf") as p:
        streams = {hashlib.sha1(x.read_raw_bytes()).hexdigest(): (int(x.Width), int(x.Height))
                   for page in p.pages for _, x in page.images.items()}
    assert streams[hashlib.sha1(upright[0].read_bytes()).hexdigest()] == (1600, 2400)
    assert hashlib.sha1((book_dir / "cache" / f"{A}.jpg").read_bytes()).hexdigest() in streams   # A untouched
    assert check.check(book)["ok"]


def test_exif_orientation_pdfx1a(book_dir: Path, cmyk_icc: Path):
    _set_output(book_dir, standard="pdf/x-1a", icc=str(cmyk_icc))
    _exif_rotated_jpeg(book_dir / "cache" / f"{B}.jpg", 2400, 1600, 6)
    book = spec.load(book_dir / "book.yaml")
    rep = render.render(book)
    assert not any(i["code"] == "ORIENTATION_REENCODED" for i in rep["issues"])   # one conversion anyway
    b = [i for i in rep["images"] if i["photo"] == B]
    assert b and all(i["source_px"] == [1600, 2400] and i["px"][0] < i["px"][1] for i in b)
    for f in (book_dir / "cache" / "cmyk").glob("*.cmyk.jpg"):
        with Image.open(f) as im:
            assert "exif" not in im.info               # upright pixels, nothing left for anyone to rotate
    assert check.check(book)["ok"]


def test_relative_icc_resolves_against_book_dir(book_dir: Path, tmp_path_factory, monkeypatch):
    import shutil as sh
    from importlib.resources import files as res
    (book_dir / "profiles").mkdir()
    sh.copy(str(res("zinegen") / "templates" / "sRGB2014.icc"), book_dir / "profiles" / "out.icc")
    _set_output(book_dir, icc="profiles/out.icc")
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    book = spec.load(book_dir / "book.yaml")
    rep = render.render(book)
    assert rep["icc"] == str((book_dir / "profiles" / "out.icc").resolve())
    assert check.check(book)["ok"]


def _set_output(book_dir: Path, **output):
    doc = yaml.safe_load((book_dir / "book.yaml").read_text())
    doc["output"].update(output)
    (book_dir / "book.yaml").write_text(yaml.safe_dump(doc))


@pytest.mark.parametrize("standard", ["pdf/x-4", "pdf/x-1a"])
def test_unresolvable_icc_fails_render(book_dir: Path, standard: str):
    _set_output(book_dir, standard=standard, icc="NoSuchProfile")
    book = spec.load(book_dir / "book.yaml")
    with pytest.raises(ValueError, match="'NoSuchProfile' not found"):
        render.render(book)
    assert not (book_dir / "out" / "interior.pdf").exists()


def test_unset_icc_on_pdfx4_is_reported(book_dir: Path):
    _set_output(book_dir, icc=None)
    book = spec.load(book_dir / "book.yaml")
    rep = render.render(book)
    codes = {i["code"]: i["level"] for i in rep["issues"]}
    assert codes.get("NO_OUTPUT_PROFILE") == "error"
    assert not check.check(book)["ok"]


def _output(book_dir: Path, request, kind: str):
    """(standard, icc) for a parametrised marks test; the CMYK cases need the cmyk_icc fixture."""
    std = "pdf/x-1a" if kind == "x-1a" else "pdf/x-4"
    icc = str(request.getfixturevalue("cmyk_icc")) if kind in ("x-1a", "x-4-cmyk") else None
    if icc:
        _set_output(book_dir, standard=std, icc=icc)
    return std


def _boxes(page) -> dict:
    return {k: [float(v) for v in page.obj[k]] for k in ("/MediaBox", "/BleedBox", "/TrimBox")}


def _forms(page):
    """Every Form XObject reachable from a page, nested ones included."""
    stack, out = [page.Resources], []
    while stack:
        res = stack.pop()
        for _, x in (res.get("/XObject") or {}).items():
            if x.get("/Subtype") == "/Form":
                out.append(x)
                if "/Resources" in x:
                    stack.append(x.Resources)
    return out


@pytest.mark.parametrize("kind", ["x-4", "x-1a", "x-4-cmyk"])
def test_marks_are_pdfx_safe(book_dir: Path, request, kind: str):
    """Default `marks: true`: crop marks and registration targets in the registration colour, no transparency
    group, no ExtGState alpha, no DeviceRGB, all of it outside the BleedBox, and the MediaBox holds them."""
    import pikepdf
    std = _output(book_dir, request, kind)
    book = spec.load(book_dir / "book.yaml")
    assert book.marks
    render.render(book)
    res = check.check(book)
    assert res["ok"], res["issues"]
    # no false positive from the deeper walk (inline images, annotations, Type 3, device colour vs intent)
    assert not {i["code"] for i in res["issues"]} & {"TRANSPARENCY", "VECTOR_COLORSPACE", "IMAGE_COLORSPACE",
                                                       "FONT_NOT_EMBEDDED", "BLEED_SMALL"}
    with pikepdf.open(book_dir / "out" / "interior.pdf") as p:
        for page in p.pages:
            b = _boxes(page)
            assert b["/MediaBox"][0] < b["/BleedBox"][0] < b["/TrimBox"][0]
            assert b["/MediaBox"][3] > b["/BleedBox"][3] > b["/TrimBox"][3]
            forms = _forms(page)
            assert len(forms) == 1                                   # only our marks
            mk = forms[0]
            assert "/Group" not in mk
            cs = mk.Resources.ColorSpace.Reg
            assert str(cs[0]) == "/Separation" and str(cs[1]) == "/All"
            assert str(cs[2]) == ("/DeviceCMYK" if kind != "x-4" else "/DeviceGray")
            ops = pikepdf.parse_content_stream(mk)
            assert not {str(op) for _, op in ops} & {"rg", "RG", "gs", "k", "K"}
            # every point a mark passes through lies outside the BleedBox (marks are in trim coordinates)
            tb, bb = b["/TrimBox"], b["/BleedBox"]
            for operands, op in ops:
                if str(op) in ("m", "l", "c"):
                    pts = [float(v) for v in operands]
                    for x, y in zip(pts[::2], pts[1::2], strict=True):
                        x, y = x + tb[0], y + tb[1]
                        assert not (bb[0] < x < bb[2] and bb[1] < y < bb[3]), (x, y)
                        assert b["/MediaBox"][0] <= x <= b["/MediaBox"][2]
            content = b"".join(c.read_bytes() for c in page.Contents) if isinstance(page.Contents, pikepdf.Array) \
                else page.Contents.read_bytes()
            assert b"rg\n" not in content and b" RG\n" not in content
    assert std == book.output["standard"]


@pytest.mark.parametrize("kind", ["x-4", "x-1a", "x-4-cmyk"])
def test_text_colour_matches_output_intent(book_dir: Path, request, kind: str):
    """Text is pure K (DeviceCMYK) on a CMYK intent; on an RGB intent, where PDF/X-4 does not allow DeviceCMYK,
    it is the same lightness in the intent profile (ICCBased), never DeviceCMYK or DeviceRGB."""
    import pikepdf
    _output(book_dir, request, kind)
    _set_page(book_dir, 5, text="colophon text")
    book = spec.load(book_dir / "book.yaml")
    render.render(book)
    assert check.check(book)["ok"]
    selected = set()
    with pikepdf.open(book_dir / "out" / "interior.pdf") as p:
        for page in p.pages:
            for operands, op in pikepdf.parse_content_stream(page):
                assert str(op) not in ("k", "K", "rg", "RG")
                if str(op) in ("cs", "CS"):
                    selected.add(str(operands[0]))
                    if kind == "x-4":
                        icc = page.Resources.ColorSpace[str(operands[0])]
                        assert str(icc[0]) == "/ICCBased" and int(icc[1].N) == 3
    assert selected == ({"/--press"} if kind == "x-4" else {"/DeviceCMYK"})


def test_ink():
    assert render.ink(1, ["c", "m", "y", "k"]) == "device-cmyk(0 0 0 1)"
    assert render.ink(0.85, None) == "device-cmyk(0 0 0 0.85)"
    assert render.ink(0.85, ["r", "g", "b"]) == "color(--press 0.15 0.15 0.15)"
    assert render.ink(1, ["gray"]) == "color(--press 0)"


def test_marks_off(book_dir: Path):
    import pikepdf
    doc = yaml.safe_load((book_dir / "book.yaml").read_text())
    doc["marks"] = False
    (book_dir / "book.yaml").write_text(yaml.safe_dump(doc))
    book = spec.load(book_dir / "book.yaml")
    render.render(book)
    assert check.check(book)["ok"]
    with pikepdf.open(book_dir / "out" / "interior.pdf") as p:
        for page in p.pages:
            b = _boxes(page)
            assert b["/MediaBox"] == b["/BleedBox"]
            assert _forms(page) == []


@pytest.mark.parametrize("marks", [True, False])
@pytest.mark.parametrize("kind", ["x-4", "x-1a"])
@pytest.mark.parametrize("bleed", [5, 10])
def test_bleed_above_10pt(book_dir: Path, request, kind: str, marks: bool, bleed: float):
    """WeasyPrint writes the BleedBox as trim ± min(bleed, 10 pt) although it lays the page out for the full
    bleed; postprocess sets the real one. The full-bleed photo's clip reaches it and the marks stay outside."""
    import pikepdf
    _output(book_dir, request, kind)
    doc = yaml.safe_load((book_dir / "book.yaml").read_text())
    doc.update(bleed_mm=bleed, marks=marks)
    (book_dir / "book.yaml").write_text(yaml.safe_dump(doc))
    book = spec.load(book_dir / "book.yaml")
    render.render(book)
    res = check.check(book)
    assert res["ok"], res["issues"]
    assert "BLEED_SMALL" not in {i["code"] for i in res["issues"]}
    b_pt = bleed * 72 / 25.4
    with pikepdf.open(book_dir / "out" / "interior.pdf") as p:
        for page in p.pages:
            b = _boxes(page)
            tb, bb, mb = b["/TrimBox"], b["/BleedBox"], b["/MediaBox"]
            assert bb == pytest.approx([tb[0] - b_pt, tb[1] - b_pt, tb[2] + b_pt, tb[3] + b_pt], abs=0.01)
            assert mb[0] <= bb[0] and mb[1] <= bb[1] and mb[2] >= bb[2] and mb[3] >= bb[3]
            if not marks:
                assert mb == bb
        # page 2 is full-bleed: its photo is clipped to the bleed rectangle (WeasyPrint px, 0.75 pt each)
        clip = next(ops for ops, op in pikepdf.parse_content_stream(p.pages[1]) if str(op) == "re")
        x, y, w, h = (float(v) * 0.75 for v in clip)
        assert (x, w) == (pytest.approx(-b_pt, abs=0.01), pytest.approx(tb[2] - tb[0] + 2 * b_pt, abs=0.01))


@pytest.mark.parametrize("kind", ["x-1a", "x-4-cmyk", "x-4"])
def test_check_catches_weasyprint_marks(book_dir: Path, request, kind: str):
    """The PDF zinegen 0.1.0 wrote with `marks: true`: WeasyPrint's `marks: crop cross` (DeviceRGB strokes in a
    Form XObject with a transparency group and a `ca 0` ExtGState). The check must see into the Form."""
    _output(book_dir, request, kind)
    book = spec.load(book_dir / "book.yaml")
    render.render(book)
    html = (book_dir / "out" / "interior.html").read_text()
    html = html.replace("bleed: 3.0mm;", "bleed: 3.0mm; marks: crop cross;", 1)
    assert "marks: crop cross" in html
    pdf = book_dir / "out" / "interior.pdf"
    weasyprint.HTML(string=html, base_url=str(book_dir / "out" / "interior.html")).write_pdf(
        pdf, pdf_variant=book.output["standard"], output_intent="--press", jpeg_quality=None, dpi=None,
        optimize_images=False)
    res = check.check(book)
    found = {(i["code"], i["level"]) for i in res["issues"]}
    if kind == "x-4":           # RGB output intent: DeviceRGB is allowed, live transparency is reported
        assert ("TRANSPARENCY", "warning") in found
        assert not any(c == "VECTOR_COLORSPACE" for c, _ in found)
    else:
        assert not res["ok"]
        # evidence from inside the marks Form, not the page-level ICC/Lab resources this unpostprocessed PDF
        # also declares (those alone would raise VECTOR_COLORSPACE on x-1a)
        assert any(i["code"] == "VECTOR_COLORSPACE" and i["level"] == "error"
                   and "DeviceRGB colour operator(s) in form XObject" in i["message"] for i in res["issues"])
        assert any(i["code"] == "TRANSPARENCY" and i["level"] == ("error" if kind == "x-1a" else "warning")
                   and "transparency group in form XObject" in i["message"] for i in res["issues"])
    assert any("form XObject" in i["message"] for i in res["issues"] if i["code"] == "TRANSPARENCY")


def _set_page(book_dir: Path, index: int, **fields):
    doc = yaml.safe_load((book_dir / "book.yaml").read_text())
    doc["pages"][index].update(fields)
    (book_dir / "book.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))


def test_long_caption_is_reported_and_clipped_between_lines(book_dir: Path):
    """A caption box holds exactly two lines, so a third is cut off whole (never through a glyph), and the
    loss is reported as TEXT_CLIPPED instead of passing silently."""
    import re

    from zinegen import layout
    _set_page(book_dir, 2, caption=" ".join(["A caption that is far too long for two lines."] * 6))
    book = spec.load(book_dir / "book.yaml")
    rep = render.render(book)
    clipped = [i for i in rep["issues"] if i["code"] == "TEXT_CLIPPED"]
    assert [(i["page"], i["level"]) for i in clipped] == [(3, "warning")]
    assert "caption needs" in clipped[0]["message"] and "holds 2" in clipped[0]["message"]
    html = (book_dir / "out" / "interior.html").read_text()
    h = float(re.search(r'data-page="3" data-kind="caption" style="[^"]*height:([\d.]+)mm', html).group(1))
    assert h == pytest.approx(2 * layout.line_mm(book, "caption"), abs=0.001)
    assert check.check(book)["ok"]                      # a warning, not an error


def test_keep_all_joins_korean_words():
    wj = render.WORD_JOINER
    assert render.keep_all("가을 산책") == f"가{wj}을 산{wj}책"
    assert render.keep_all("2024년 가을.") == f"2024{wj}년 가{wj}을."
    assert render.keep_all("plain text, 1 2") == "plain text, 1 2"


def test_korean_breaks_only_between_words():
    """WeasyPrint drops `word-break: keep-all`; the word joiners make lines break at spaces only, and
    `overflow-wrap: anywhere` still breaks a word wider than the box."""
    from weasyprint.formatting_structure import boxes
    text = "가을 산책길에서 만난 오래된 느티나무와 그 아래 앉아 있던 할머니의 이야기 " + "아주" * 12 + "긴단어"

    def lines(t):
        html = ("<style>@page { size: 60mm 120mm; margin: 5mm } div { font-size: 10pt; width: 40mm; "
                f"white-space: pre-wrap; overflow-wrap: anywhere }}</style><div>{t}</div>")
        page = weasyprint.HTML(string=html).render().pages[0]._page_box
        return ["".join(x.text for x in lb.descendants() if isinstance(x, boxes.TextBox)).replace(render.WORD_JOINER, "")
                for lb in page.descendants() if isinstance(lb, boxes.LineBox)]
    out = lines(render.keep_all(text))
    words = text.split()
    long_word = words[-1]
    for ln in out:
        parts = ln.split()
        assert all(p in words or p in long_word for p in parts), out
    assert "".join(out).replace(" ", "") == text.replace(" ", "")
    assert any(ln.strip() != long_word and ln.strip() in long_word for ln in out)    # the long word was broken


@pytest.mark.skipif(not shutil.which("gs"), reason="ghostscript not installed")
def test_proof_spreads(book_dir: Path):
    from zinegen import proof
    book = spec.load(book_dir / "book.yaml")
    render.render(book)
    out = proof.proof(book, dpi=20)
    assert len(out) == 5          # 8 pages: [1], [2,3], [4,5], [6,7], [8]
    assert out[0].name == "spread-01.png"


def test_icc_name_is_not_shadowed_by_a_book_file(book_dir: Path):
    """`icc: X` without a separator or extension is a profile name: a file `X` next to book.yaml is ignored."""
    (book_dir / "NoSuchProfileName").write_bytes(b"not an icc profile")
    _set_output(book_dir, icc="NoSuchProfileName")
    with pytest.raises(ValueError, match="not found"):
        render.resolve_icc(spec.load(book_dir / "book.yaml"))


def test_icc_error_messages_are_not_redundant(book_dir: Path):
    (book_dir / "bad.icc").write_bytes(b"not an icc profile")
    absolute = str(book_dir / "bad.icc")
    _set_output(book_dir, icc=absolute)
    with pytest.raises(ValueError) as exc:
        render.resolve_icc(spec.load(book_dir / "book.yaml"))
    assert str(exc.value).count(absolute) == 1 and str(exc.value).startswith("output.icc /")
    _set_output(book_dir, icc="NoSuchProfile.icc")
    with pytest.raises(ValueError) as exc:
        render.resolve_icc(spec.load(book_dir / "book.yaml"))
    assert ".icc.icc" not in str(exc.value) and "no NoSuchProfile.icc exists" in str(exc.value)


def test_unreadable_icc_is_a_clear_error(book_dir: Path, capsys):
    (book_dir / "bad.icc").write_bytes(b"not an icc profile")
    _set_output(book_dir, icc="bad.icc")
    with pytest.raises(ValueError, match="not a readable ICC profile"):
        render.render(spec.load(book_dir / "book.yaml"))
    from zinegen import cli
    assert cli.main(["render", str(book_dir / "book.yaml")]) == 2
    err = capsys.readouterr().err
    assert "error: output.icc 'bad.icc' resolves to" in err and "not a readable ICC profile" in err
    assert "Traceback" not in err
