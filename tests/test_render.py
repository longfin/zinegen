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


@pytest.mark.skipif(not shutil.which("gs"), reason="ghostscript not installed")
def test_proof_spreads(book_dir: Path):
    from zinegen import proof
    book = spec.load(book_dir / "book.yaml")
    render.render(book)
    out = proof.proof(book, dpi=20)
    assert len(out) == 5          # 8 pages: [1], [2,3], [4,5], [6,7], [8]
    assert out[0].name == "spread-01.png"
