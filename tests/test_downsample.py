"""PDF/X-1a downsampling (issue #5): target pixel sizes from the layout, the CMYK cache key, and the
single conversion. No network; the conversion test needs a CMYK press profile and is skipped without one."""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import pytest
import yaml
from PIL import Image

from zinegen import color, layout, render, spec

A = "00000000000000000000000000000001"
B = "00000000000000000000000000000002"
SRGB = Path(str(files("zinegen") / "templates" / "sRGB2014.icc"))


def make_book(tmp_path: Path, pages: list[dict], **output) -> spec.Book:
    doc = {"version": 1, "title": "T", "trim": {"preset": "A5"}, "bleed_mm": 3, "binding": "perfect",
           "output": {"standard": "pdf/x-1a", **output}, "pages": pages}
    (tmp_path / "book.yaml").write_text(yaml.safe_dump(doc))
    return spec.load(tmp_path / "book.yaml")


def placed_dpi(book, sizes, targets) -> dict[str, list[float]]:
    """Lay out with source sizes, then measure each placement with the embedded (target) pixels."""
    out: dict[str, list[float]] = {}
    for pl in layout.layout(book, sizes):
        for im in pl.images:
            im.px = targets[im.photo]
            out.setdefault(im.photo, []).append(im.dpi)
    return out


def test_full_bleed_with_focal_sized_for_whole_scaled_image(tmp_path):
    book = make_book(tmp_path, [{"template": "full-bleed", "photo": A, "focal": [0.2, 0.5]}])
    sizes = {A: (6000, 4000)}
    t = render.target_sizes(layout.layout(book, sizes), sizes, 350)
    # A5 + 3 mm bleed = 154 × 216 mm; a 3:2 cover placement is 324 mm wide (most of it cropped away),
    # and every visible pixel must still be 350 dpi: 324 / 25.4 * 350 = 4464.6 -> 4465 px
    assert t[A] == (4465, 2977)
    assert min(placed_dpi(book, sizes, t)[A]) >= 350


def test_photo_used_twice_sized_for_largest_placement(tmp_path):
    book = make_book(tmp_path, [{"template": "margin", "photo": A}, {"template": "blank"},
                                {"template": "spread-full", "photo": A}])
    sizes = {A: (9000, 6000)}
    t = render.target_sizes(layout.layout(book, sizes), sizes, 350)
    dpis = placed_dpi(book, sizes, t)[A]
    assert round(min(dpis)) == 350                  # the spread is the binding placement
    assert max(dpis) > 350                          # the margin placement simply gets more
    assert t[A][0] < 9000


def test_never_upsamples_and_disabled(tmp_path):
    book = make_book(tmp_path, [{"template": "full-bleed", "photo": A}, {"template": "margin", "photo": B}])
    sizes = {A: (1200, 800), B: (6000, 4000)}
    pages = layout.layout(book, sizes)
    t = render.target_sizes(pages, sizes, 350)
    assert t[A] == (1200, 800)                      # already below 350 dpi: untouched
    assert t[B][0] < 6000 and t[B][1] < 4000
    assert render.target_sizes(pages, sizes, None) == sizes


def test_aspect_kept_within_a_pixel(tmp_path):
    book = make_book(tmp_path, [{"template": "pair", "photos": [A, B]}])
    sizes = {A: (6000, 4000), B: (3333, 5000)}
    t = render.target_sizes(layout.layout(book, sizes), sizes, 350)
    for pid, (w, h) in sizes.items():
        tw, th = t[pid]
        assert tw < w and abs(tw - th * w / h) <= 1


def test_max_dpi_setting(tmp_path):
    assert render.max_dpi(make_book(tmp_path, [{"template": "blank"}])) == spec.DEFAULT_MAX_DPI == 350
    assert render.max_dpi(make_book(tmp_path, [{"template": "blank"}], max_dpi=300)) == 300
    assert render.max_dpi(make_book(tmp_path, [{"template": "blank"}], max_dpi=None)) is None
    with pytest.raises(spec.SpecError, match="max_dpi"):
        make_book(tmp_path, [{"template": "blank"}], max_dpi=50)


def test_cmyk_key_covers_everything_that_changes_output(tmp_path):
    src = tmp_path / "a.jpg"
    Image.new("RGB", (64, 64), (10, 20, 30)).save(src, quality=90)
    base = color.cmyk_key(src, SRGB, "perceptual", 95, (32, 32))
    assert base == color.cmyk_key(src, SRGB, "perceptual", 95, (32, 32))
    assert base != color.cmyk_key(src, SRGB, "perceptual", 95, (33, 33))
    assert base != color.cmyk_key(src, SRGB, "perceptual", 95, None)
    assert base != color.cmyk_key(src, SRGB, "relative-colorimetric", 95, (32, 32))
    assert base != color.cmyk_key(src, SRGB, "perceptual", 90, (32, 32))
    other = tmp_path / "p.icc"
    other.write_bytes(SRGB.read_bytes() + b"x")
    assert base != color.cmyk_key(src, other, "perceptual", 95, (32, 32))
    # a re-fetched rendition that differs only after the first MiB must not hit the old entry
    big = tmp_path / "big.jpg"
    big.write_bytes(b"\0" * (1 << 21))
    k1 = color.cmyk_key(big, SRGB, "perceptual", 95, None)
    big.write_bytes(b"\0" * ((1 << 21) - 1) + b"\1")
    assert k1 != color.cmyk_key(big, SRGB, "perceptual", 95, None)


def test_to_cmyk_resizes_once_and_caches(tmp_path, cmyk_icc):
    src = tmp_path / "a.jpg"
    Image.linear_gradient("L").resize((800, 600)).convert("RGB").save(src, quality=90, icc_profile=SRGB.read_bytes())
    d = tmp_path / "cmyk"
    out = color.to_cmyk(src, cmyk_icc, d, size=(400, 300))
    with Image.open(out) as im:
        assert im.mode == "CMYK" and im.size == (400, 300)
        assert "icc_profile" not in im.info          # DeviceCMYK in the PDF; the output intent has the profile
    mtime = out.stat().st_mtime_ns
    assert color.to_cmyk(src, cmyk_icc, d, size=(400, 300)) == out and out.stat().st_mtime_ns == mtime
    full = color.to_cmyk(src, cmyk_icc, d)
    assert color.to_cmyk(src, cmyk_icc, d, size=(2000, 1500)) == full   # clamped, no upsample
    with Image.open(full) as im:
        assert im.size == (800, 600)


def test_prune_keeps_only_used_conversions(tmp_path):
    cmyk = tmp_path / "cache" / "cmyk"
    cmyk.mkdir(parents=True)
    used = cmyk / f"{A}.aaaaaaaaaaaaaaaa.cmyk.jpg"
    stale = cmyk / f"{A}.bbbbbbbbbbbbbbbb.cmyk.jpg"
    other = cmyk / f"{B}.cccccccccccccccc.cmyk.jpg"
    for f in (used, stale, other):
        f.write_bytes(b"x")
        f.with_suffix(".json").write_text("{}")
    unrelated = cmyk / "notes.txt"
    unrelated.write_text("keep")
    source = tmp_path / "cache" / f"{A}.jpg"
    source.write_bytes(b"src")
    render._prune(cmyk, {used}, "cmyk")
    assert used.exists() and used.with_suffix(".json").exists()
    assert not stale.exists() and not stale.with_suffix(".json").exists()
    assert not other.exists() and not other.with_suffix(".json").exists()
    assert unrelated.exists() and source.exists()


def test_slightly_above_target_is_not_resampled(tmp_path):
    book = make_book(tmp_path, [{"template": "full-bleed", "photo": A}])
    # 324 mm cover placement: 4465 px is 350 dpi; within RESAMPLE_TOLERANCE above that, keep the source
    keep = int(4465 * render.RESAMPLE_TOLERANCE) - 5
    sizes = {A: (keep, keep * 2 // 3)}
    assert render.target_sizes(layout.layout(book, sizes), sizes, 350)[A] == sizes[A]
    over = int(4465 * render.RESAMPLE_TOLERANCE) + 50
    sizes = {A: (over, over * 2 // 3)}
    t = render.target_sizes(layout.layout(book, sizes), sizes, 350)
    assert t[A][0] < over and round(min(placed_dpi(book, sizes, t)[A])) == 350


def _x4_hint(tmp_path, pages, sizes, **source):
    doc_book = make_book(tmp_path, pages, standard="pdf/x-4")
    doc_book.source.update(source)
    issues = render._dpi_above_need(doc_book, layout.layout(doc_book, sizes))
    return issues[0].message if issues else None


def test_dpi_above_need_suggests_size_only_when_it_stays_printable(tmp_path):
    # margin placement on A5 (~120 mm wide): 6000 px is ~1270 dpi, 2048 px would still be ~430 dpi
    msg = _x4_hint(tmp_path, [{"template": "margin", "photo": A}], {A: (6000, 4000)})
    assert "1 of 1 placements" in msg and "source.size: 1280" not in msg and "source.size: 2048" in msg
    # a full-bleed cover (324 mm) at 6000 px is ~470 dpi, and 2560 px would drop it to ~200: no advice
    msg = _x4_hint(tmp_path, [{"template": "full-bleed", "photo": A}, {"template": "margin", "photo": B}],
                   {A: (6000, 4000), B: (9000, 6000)})
    assert msg and "source.size" not in msg
    # spread photos count once, not once per page
    msg = _x4_hint(tmp_path, [{"template": "blank"}, {"template": "spread-margin", "photo": A}], {A: (20000, 8000)})
    assert "1 of 1 placements" in msg
    # already at a small size: nothing smaller is suggested unless it fits
    assert _x4_hint(tmp_path, [{"template": "margin", "photo": A}], {A: (2048, 1365)}, size="2048") is None


def test_resample_tolerance_boundary(tmp_path):
    """Just under RESAMPLE_TOLERANCE above the target: converted at source size; just over: resampled."""
    book = make_book(tmp_path, [{"template": "margin", "photo": A}])
    probe = {A: (3000, 2000)}
    need = render.target_sizes(layout.layout(book, probe), probe, 350)[A][0] / 3000   # scale for 350 dpi
    # source width w gives w / (need * 3000) times the target dpi on this placement (aspect fixed, same box)
    for factor, resampled in ((1.095, False), (1.105, True)):   # the documented 1.1x tolerance
        w = round(need * 3000 * factor)
        sizes = {A: (w, round(w * 2 / 3))}
        t = render.target_sizes(layout.layout(book, sizes), sizes, 350)[A]
        assert (t != sizes[A]) is resampled, (factor, t, sizes[A])
        if resampled:
            assert round(min(placed_dpi(book, sizes, {A: t})[A])) == 350


def test_unplaced_photo_is_not_converted(tmp_path, cmyk_icc):
    """A photo whose page has no room (NO_ROOM) has no placement: no conversion, nothing kept for it."""
    book = make_book(tmp_path, [{"template": "margin", "photo": A},
                                {"template": "pair", "photos": [A, B], "gap_mm": 200}], icc=str(cmyk_icc))
    sizes = {A: (600, 400), B: (600, 400)}
    pages = layout.layout(book, sizes)
    assert render.placed(pages) == [A]
    cache = tmp_path / "cache"
    cache.mkdir()
    srcs = {}
    for pid in (A, B):
        srcs[pid] = cache / f"{pid}.jpg"
        Image.new("RGB", sizes[pid], (200, 100, 50)).save(srcs[pid], quality=90)
    info = {pid: color.image_info(f) for pid, f in srcs.items()}
    files, emb, _, _ = render.prepare_images(book, pages, srcs, info, cmyk_icc)
    assert set(files) == {A} and set(emb) == {A}
    assert [f.name.split(".")[0] for f in (cache / "cmyk").glob("*.cmyk.jpg")] == [A]


def test_find_profile_relative_to_book_dir(tmp_path, monkeypatch):
    book = tmp_path / "book"
    (book / "profiles").mkdir(parents=True)
    prof = book / "profiles" / "press.icc"
    prof.write_bytes(SRGB.read_bytes())
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "profiles").mkdir(parents=True)
    (elsewhere / "profiles" / "press.icc").write_bytes(SRGB.read_bytes())
    monkeypatch.chdir(elsewhere)
    assert color.find_profile("profiles/press.icc", base=book) == prof.resolve()
    assert color.find_profile("profiles/nope.icc", base=book) is None      # no fallback to the cwd
    assert color.find_profile(str(prof), base=elsewhere) == prof.resolve()  # absolute paths unchanged
    assert color.find_profile("profiles/press.icc") == (elsewhere / "profiles" / "press.icc").resolve()
