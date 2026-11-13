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


@pytest.mark.skipif(not shutil.which("gs"), reason="ghostscript not installed")
def test_proof_spreads(book_dir: Path):
    from zinegen import proof
    book = spec.load(book_dir / "book.yaml")
    render.render(book)
    out = proof.proof(book, dpi=20)
    assert len(out) == 5          # 8 pages: [1], [2,3], [4,5], [6,7], [8]
    assert out[0].name == "spread-01.png"

