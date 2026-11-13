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


def synthetic_jpeg(path: Path, w: int, h: int):
    im = Image.linear_gradient("L").resize((w, h)).convert("RGB")
    icc = (files("zinegen") / "templates" / "sRGB2014.icc").read_bytes()
    im.save(path, quality=90, icc_profile=icc)


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
    rep = render.render(book)
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


def test_missing_image_fails_render(book_dir: Path, monkeypatch):
    """A cache file that disappears between conversion and write_pdf (e.g. pruned by a concurrent render)
    is only a WeasyPrint warning; render must not return a PDF with a photo missing."""
    real = render.prepare_images

    def vanish(*a, **k):
        files, sizes, info = real(*a, **k)
        victim = book_dir / "gone.jpg"
        victim.write_bytes(files[B].read_bytes())
        files = {**files, B: victim}
        victim.unlink()
        return files, sizes, info

    monkeypatch.setattr(render, "prepare_images", vanish)
    book = spec.load(book_dir / "book.yaml")
    with pytest.raises(RuntimeError, match=f"missing from the PDF \\({B}\\)"):
        render.render(book)
    assert not (book_dir / "out" / "interior.pdf").exists()


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
