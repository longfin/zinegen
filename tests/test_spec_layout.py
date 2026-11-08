import re
from pathlib import Path

import pytest
import yaml

from zinegen import layout, presets, spec
from zinegen.issues import Issue

A = "0123456789abcdef0123456789abcdef"
B = "fedcba9876543210fedcba9876543210"


def write(tmp_path: Path, pages, **extra):
    doc = {"version": 1, "title": "t", "trim": {"preset": "A5"}, "binding": "perfect", "output": {"standard": "pdf/x-4"}, "pages": pages}
    doc.update(extra)
    p = tmp_path / "book.yaml"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True))
    return p


def test_presets_and_spine():
    assert presets.resolve_trim("a5") == (148, 210)
    assert presets.resolve_trim({"width_mm": 200, "height_mm": 200}) == (200, 200)
    assert presets.spine_mm(100, 110) == 5.5
    assert presets.spine_mm(100, 110, "saddle") == 0
    assert presets.round_up_pages(27, "perfect") == 28
    assert presets.round_up_pages(27, "saddle") == 28
    with pytest.raises(ValueError):
        presets.resolve_trim("nope")


def test_spread_moves_to_verso_and_padding(tmp_path):
    p = write(tmp_path, [{"template": "title", "title": "x"}, {"template": "spread-full", "photo": A}])
    book = spec.load(p)
    templates = [(pg.index, pg.template) for pg in book.pages]
    assert templates[0] == (1, "title")
    assert templates[1] == (2, "spread-full")
    assert book.page_count == 4  # 3 pages padded to perfect binding multiple of 2
    assert {i.code for i in book.issues} == {"PAGES_PADDED"}
    assert book.pages[1].source == 1 and book.pages[-1].source is None


def test_spread_needs_blank_when_on_recto(tmp_path):
    p = write(tmp_path, [{"template": "blank"}, {"template": "blank"}, {"template": "spread-margin", "photo": A}])
    book = spec.load(p)
    assert [(pg.index, pg.template) for pg in book.pages][:4] == [(1, "blank"), (2, "blank"), (3, "blank"), (4, "spread-margin")]
    assert [i.code for i in book.issues][0] == "SPREAD_MOVED"


def test_layout_geometry(tmp_path):
    p = write(tmp_path, [{"template": "full-bleed", "photo": A, "focal": [0, 0]},
                         {"template": "margin", "photo": B, "caption": "hi"},
                         {"template": "spread-full", "photo": A}])
    book = spec.load(p)
    pages = layout.layout(book, {A: (6000, 4000), B: (4000, 6000)})
    fb = pages[0].images[0]
    assert (fb.x, fb.y) == (-3, -3) and abs(fb.h - 216) < 1e-6 and fb.w > 154
    mg = pages[1].images[0]
    assert mg.x >= 12 and mg.y == 14 and mg.h < 210 - 14 - 16
    assert pages[1].texts[0].kind == "caption"
    spread = [pl for pl in pages if pl.template == "spread-full"]
    assert [pl.index for pl in spread] == [4, 5]
    assert abs(spread[1].images[0].x - (spread[0].images[0].x - 148)) < 1e-9
    assert spread[0].images[0].crossing_gutter
    rep = layout.report(book, pages)
    assert rep["version"] == 1 and rep["pages"] == 6
    codes = {i["code"] for i in rep["issues"]}
    assert "GUTTER_CROSSING" in codes and "PAGES_PADDED" in codes
    assert all(i["dpi"] > 0 for i in rep["images"])
    assert all({"page", "photo", "template", "placed_mm", "px", "dpi", "level", "crossing_gutter"} <= set(i) for i in rep["images"])


@pytest.mark.parametrize("pages, fragment", [
    ([{"template": "margin"}], "pages[0] (margin): 'photo' is a required property"),
    ([{"template": "pair", "photos": [A]}], "pages[0] (pair) photos"),
    ([{"template": "whatever"}], "pages[0].template"),
    ([{"template": "margin", "photo": A, "focal_point": [0, 0]}], "Additional properties"),
    ([{"template": "full-bleed", "photo": "short"}], "does not match"),
])
def test_validation_errors(tmp_path, pages, fragment):
    with pytest.raises(spec.SpecError, match=re.escape(fragment)):
        spec.load(write(tmp_path, pages))


def test_top_level_validation(tmp_path):
    with pytest.raises(spec.SpecError, match="version"):
        p = tmp_path / "book.yaml"
        p.write_text(yaml.safe_dump({"pages": []}))
        spec.load(p)
    with pytest.raises(spec.SpecError, match="trim"):
        spec.load(write(tmp_path, [], trim={"width_mm": 100}))
    with pytest.raises(spec.SpecError, match="binding"):
        spec.load(write(tmp_path, [], binding="glue"))
    with pytest.raises(spec.SpecError, match="Additional properties"):
        spec.load(write(tmp_path, [], margin_mm={"top": 1}))
    book = spec.load(write(tmp_path, [{"template": "blank", "note": "free text"}], trim="SQ210", paper={"caliper_um": 110}))
    assert (book.trim_w, book.trim_h) == (210, 210)


def test_issue_codes():
    assert Issue("LOW_DPI", "x", page=3, photo=A).to_dict() == {"code": "LOW_DPI", "message": "x", "level": "warning", "page": 3, "photo": A}
    with pytest.raises(ValueError):
        Issue("NOPE", "x")
