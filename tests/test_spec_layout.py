from pathlib import Path

import pytest

from zinegen import layout, presets, spec


def write(tmp_path: Path, pages, **extra):
    import yaml
    doc = {"title": "t", "trim": {"preset": "A5"}, "binding": "perfect", "output": {"standard": "pdf/x-4"}, "pages": pages}
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
    p = write(tmp_path, [{"template": "title", "title": "x"}, {"template": "spread-full", "photo": "a"}])
    book = spec.load(p)
    # title=1, auto blank=2, spread=3? no: spread must start on an even page -> blank inserted at 2, spread on 2? index 2 is even
    templates = [(pg.index, pg.template) for pg in book.pages]
    assert templates[0] == (1, "title")
    assert templates[1] == (2, "spread-full")
    assert book.page_count == 4  # 3 pages padded to perfect binding multiple of 2
    assert any("padded" in w for w in book.warnings)


def test_spread_needs_blank_when_on_recto(tmp_path):
    p = write(tmp_path, [{"template": "blank"}, {"template": "blank"}, {"template": "spread-margin", "photo": "a"}])
    book = spec.load(p)
    assert [(pg.index, pg.template) for pg in book.pages][:4] == [(1, "blank"), (2, "blank"), (3, "blank"), (4, "spread-margin")]


def test_layout_geometry(tmp_path):
    p = write(tmp_path, [{"template": "full-bleed", "photo": "a", "focal": [0, 0]},
                         {"template": "margin", "photo": "b", "caption": "hi"},
                         {"template": "spread-full", "photo": "a"}])
    book = spec.load(p)
    pages = layout.layout(book, {"a": (6000, 4000), "b": (4000, 6000)})
    fb = pages[0].images[0]
    assert (fb.x, fb.y) == (-3, -3) and abs(fb.h - 216) < 1e-6 and fb.w > 154
    mg = pages[1].images[0]
    assert mg.x >= 12 and mg.y == 14 and mg.h < 210 - 14 - 16
    assert pages[1].texts[0].kind == "caption"
    # spread: page 4 (verso) and 5 (recto); recto image offset by trim width
    spread = [pl for pl in pages if pl.template == "spread-full"]
    assert [pl.index for pl in spread] == [4, 5]
    assert abs(spread[1].images[0].x - (spread[0].images[0].x - 148)) < 1e-9
    assert spread[0].images[0].crossing_gutter
    rep = layout.report(book, pages)
    assert any("gutter" in w for w in rep["warnings"])
    assert all(i["dpi"] > 0 for i in rep["images"])


def test_validation_errors(tmp_path):
    with pytest.raises(ValueError):
        spec.load(write(tmp_path, [{"template": "margin"}]))
    with pytest.raises(ValueError):
        spec.load(write(tmp_path, [{"template": "pair", "photos": ["a"]}]))
    with pytest.raises(ValueError):
        spec.load(write(tmp_path, [{"template": "whatever"}]))
