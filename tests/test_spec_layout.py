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


def _intersects(r, s):
    return r[0] < s[0] + s[2] and s[0] < r[0] + r[2] and r[1] < s[1] + s[3] and s[1] < r[1] + r[3]


def _box(el):
    return (el.x, el.y, el.w, el.h)


@pytest.mark.parametrize("item", [
    {"template": "pair", "photos": [B, B], "direction": "row"},
    {"template": "pair", "photos": [A, A], "direction": "column"},
    {"template": "pair", "photos": [A, B], "direction": "row"},
    {"template": "pair", "photos": [A, B], "direction": "column", "gap_mm": 2},
    {"template": "margin", "photo": A},
    {"template": "margin", "photo": B, "valign": "bottom"},
])
@pytest.mark.parametrize("caption_pt", [7.5, 12])
def test_caption_clear_of_photos_and_folio(tmp_path, item, caption_pt):
    """Issue #9: the caption sits below the photo block with a gap and never touches the folio (mm geometry)."""
    p = write(tmp_path, [{"template": "title", "title": "x"}, {**item, "caption": "c"}, {**item, "caption": "c"}],
              page_numbers=True, type={"caption_pt": caption_pt})
    book = spec.load(p)
    pages = layout.layout(book, {A: (6000, 4000), B: (4000, 6000)})
    for pl in pages[1:3]:
        (cap,) = [t for t in pl.texts if t.kind == "caption"]
        (folio,) = [t for t in pl.texts if t.kind == "folio"]
        block_bottom = max(im.y + im.h for im in pl.images)
        for im in pl.images:
            assert not _intersects(_box(cap), _box(im))
            assert not _intersects(_box(folio), _box(im))
        assert cap.y - block_bottom >= layout.CAPTION_GAP_MM - 1e-9
        assert not _intersects(_box(cap), _box(folio))
        assert folio.y - (cap.y + cap.h) >= 3          # clearly separate blocks, not one
        if item["template"] == "pair":
            # directly under the photo block, across the margin box
            left, _, right, _ = layout._margins(book, pl.index)
            assert abs(cap.y - block_bottom - layout.CAPTION_GAP_MM) < 1e-9
            assert (cap.x, cap.w) == (left, book.trim_w - left - right)


LONG_CAPTION = ("Seoul, autumn 2025. Two portraits stacked in a column make a narrow photo block; this caption is "
                "about one hundred and seventy characters long, like the review case.")


@pytest.mark.parametrize("trim, margins, caption_pt", [
    ({"preset": "A5"}, None, 7.5),
    ({"width_mm": 100, "height_mm": 100}, {"top": 20, "bottom": 20, "inside": 20, "outside": 20}, 14),
])
def test_narrow_column_pair_caption_uses_margin_box_width(tmp_path, trim, margins, caption_pt):
    """Two portraits in a column are a narrow block; the two-line caption box must not shrink to the block width,
    or a caption that fits at full width wraps further and is clipped (review of #12)."""
    extra = {"page_numbers": True, "trim": trim, "type": {"caption_pt": caption_pt}}
    if margins:
        extra["margins_mm"] = margins
    item = {"template": "pair", "photos": [B, B], "direction": "column", "caption": LONG_CAPTION}
    book = spec.load(write(tmp_path, [{"template": "title", "title": "x"}, item, item], **extra))
    pages = layout.layout(book, {B: (4000, 6000)})
    for pl in pages[1:3]:
        left, _, right, _ = layout._margins(book, pl.index)
        (cap,) = [t for t in pl.texts if t.kind == "caption"]
        block_x0 = min(im.x for im in pl.images)
        block_w = max(im.x + im.w for im in pl.images) - block_x0
        assert block_w < 0.6 * cap.w                                   # the block really is narrow
        assert (cap.x, cap.w) == (left, book.trim_w - left - right)    # full margin-box width, as on main
        assert cap.y > max(im.y + im.h for im in pl.images)            # still under the block
    if trim == {"preset": "A5"}:
        # ~165 characters at 7.5 pt (DejaVu Sans, ~1.3 mm per character) need ~108 mm per line for two lines
        assert len(LONG_CAPTION) > 160
        assert all(t.w >= 108 for pl in pages[1:3] for t in pl.texts if t.kind == "caption")


@pytest.mark.parametrize("item", [
    {"template": "pair", "photos": [A, A], "direction": "column", "gap_mm": 30, "caption": LONG_CAPTION},
    {"template": "pair", "photos": [A, A], "direction": "column", "gap_mm": 40, "caption": LONG_CAPTION},
    {"template": "pair", "photos": [A, A], "direction": "row", "gap_mm": 60},
    {"template": "margin", "photo": A, "caption": LONG_CAPTION, "_top": 50},
])
def test_no_room_reports_instead_of_degenerate_boxes(tmp_path, item):
    top = item.pop("_top", 20)
    p = write(tmp_path, [item], trim={"width_mm": 100, "height_mm": 100}, type={"caption_pt": 24},
              margins_mm={"top": top, "bottom": 20, "inside": 20, "outside": 20})
    book = spec.load(p)
    pages = layout.layout(book, {A: (6000, 4000)})
    assert not pages[0].images
    rep = layout.report(book, pages)
    (err,) = [i for i in rep["issues"] if i["code"] == "NO_ROOM"]
    assert err["level"] == "error" and err["page"] == 1
    assert not rep["images"]
    for pl in pages:
        for im in pl.images:
            assert im.w > 0 and im.h > 0


def test_pair_with_room_has_no_no_room_issue(tmp_path):
    book = spec.load(write(tmp_path, [{"template": "pair", "photos": [A, B], "caption": LONG_CAPTION}]))
    rep = layout.report(book, layout.layout(book, {A: (6000, 4000), B: (4000, 6000)}))
    assert "NO_ROOM" not in {i["code"] for i in rep["issues"]}
    assert all(min(i["placed_mm"]) >= layout.MIN_PHOTO_BOX_MM for i in rep["images"])


def test_pair_without_caption_uses_full_box(tmp_path):
    p = write(tmp_path, [{"template": "pair", "photos": [A, A], "direction": "column", "gap_mm": 6}])
    book = spec.load(p)
    (pl,) = [pl for pl in layout.layout(book, {A: (4000, 6000)}) if pl.template == "pair"]
    assert not pl.texts
    assert abs(pl.images[1].clip[1] + pl.images[1].clip[3] - (210 - 16)) < 1e-9


@pytest.mark.parametrize("template", ["spread-full", "spread-margin"])
def test_gutter_crossing_reported_once_per_photo(tmp_path, template):
    """Issue #11: a spread photo is placed on two pages but crosses the gutter once."""
    p = write(tmp_path, [{"template": "title", "title": "x"}, {"template": template, "photo": A, "caption": "c"}
                         if template == "spread-margin" else {"template": template, "photo": A}])
    book = spec.load(p)
    pages = layout.layout(book, {A: (6000, 4000)})
    rep = layout.report(book, pages)
    crossings = [i for i in rep["issues"] if i["code"] == "GUTTER_CROSSING"]
    assert crossings == [{"code": "GUTTER_CROSSING", "level": "warning", "message": crossings[0]["message"], "page": 2, "photo": A}]
    assert [i["page"] for i in rep["images"]] == [2, 3]     # images[] still lists the spread once per page
    assert all(i["crossing_gutter"] for i in rep["images"])


@pytest.mark.parametrize("template, px, code", [
    ("spread-full", (3600, 2400), "LOW_DPI"),     # 3600 px over ~324 mm: ~282 dpi
    ("spread-full", (2400, 1600), "BAD_DPI"),
    ("spread-margin", (2400, 1600), "LOW_DPI"),   # ~272 mm wide: ~224 dpi
    ("spread-margin", (1600, 1067), "BAD_DPI"),
])
def test_spread_dpi_reported_once_per_photo(tmp_path, template, px, code):
    """Like GUTTER_CROSSING: one dpi issue per spread photo, on the verso; images[] keeps both pages."""
    book = spec.load(write(tmp_path, [{"template": "title", "title": "x"}, {"template": template, "photo": A}]))
    rep = layout.report(book, layout.layout(book, {A: px}))
    dpi = [i for i in rep["issues"] if i["code"] in ("LOW_DPI", "BAD_DPI")]
    assert [(i["code"], i["page"], i["photo"]) for i in dpi] == [(code, 2, A)]
    level = "low" if code == "LOW_DPI" else "bad"
    assert [(i["page"], i["level"]) for i in rep["images"]] == [(2, level), (3, level)]


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
