"""Deterministic page geometry: turns the book spec into absolutely positioned elements (mm).

Every image placement is computed here (not by CSS object-fit) so that effective resolution,
bleed coverage and gutter crossing can be reported exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .issues import Issue
from .presets import BINDINGS
from .spec import Book

CAPTION_GAP_MM = 2.0     # space between a photo (block) and its caption
CAPTION_LINES = 2        # lines a caption box holds
MIN_PHOTO_BOX_MM = 5.0   # smaller photo boxes mean the margins/gap/caption leave no room: NO_ROOM, nothing placed
PT_MM = 25.4 / 72
# CSS line-height per text kind (the template uses these). Text boxes are cut to a whole number of lines, so
# text that does not fit is clipped between lines, never through one; render reports it as TEXT_CLIPPED.
LINE_HEIGHT = {"title": 1.3, "subtitle": 1.5, "heading": 1.4, "body": 1.75, "caption": 1.5, "colophon": 1.6,
               "folio": 1.2}


def font_pt(book: Book, kind: str) -> float:
    """Font size of a text kind in pt (the template uses these)."""
    t = book.type
    return float({"title": t["title_pt"], "subtitle": t["body_pt"] + 1, "heading": t["body_pt"] + 4,
                  "body": t["body_pt"]}.get(kind, t["caption_pt"]))


def line_mm(book: Book, kind: str) -> float:
    return font_pt(book, kind) * PT_MM * LINE_HEIGHT[kind]


# Text kinds whose box is the space left between other content (a text page's body under its heading, inside
# the margins). Every other box has a fixed height of its own (title, subtitle, heading, colophon, folio,
# caption) and always keeps at least one line, even when its type is larger than the box.
BOUNDED_KINDS = ("body",)


def _whole_lines(book: Book, t: TextEl, pl: PageLayout) -> TextEl:
    """Cut a text box to the whole number of lines that fit in its height.

    A fixed-height box keeps at least one line, so large type is never dropped (it may run past its nominal
    height, as it always did). A bounded box (BOUNDED_KINDS) whose space is less than one line high gets 0 lines,
    so it cannot be drawn over the margin, and the page gets a NO_ROOM error: the text is not printed and the
    book is not clean. This does not depend on render's TEXT_CLIPPED detection."""
    lh = line_mm(book, t.kind)
    n = max(0, int(t.h / lh + 1e-6))
    if n == 0 and t.kind in BOUNDED_KINDS:
        if t.text.strip():
            pl.issues.append(Issue("NO_ROOM", f"{t.kind} text has {t.h:.1f} mm of height, less than one line "
                                   f"({lh:.1f} mm); it is not printed. Reduce the margins, the type size or "
                                   "the heading", page=pl.index))
    else:
        n = max(1, n)
    t.h = n * lh
    return t


@dataclass
class ImageEl:
    photo: str
    x: float
    y: float
    w: float
    h: float
    clip: tuple[float, float, float, float]   # x, y, w, h in page mm (may extend into bleed)
    px: tuple[int, int]
    crossing_gutter: bool = False
    spread: int | None = None   # verso page index when one placement spans two pages

    @property
    def dpi(self) -> float:
        return self.px[0] / (self.w / 25.4)


@dataclass
class TextEl:
    kind: str          # title | subtitle | body | caption | colophon | folio
    text: str
    x: float
    y: float
    w: float
    h: float
    align: str = "left"


@dataclass
class PageLayout:
    index: int
    template: str
    images: list[ImageEl] = field(default_factory=list)
    texts: list[TextEl] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    def fits(self, boxes, why: str) -> bool:
        """False (and a NO_ROOM error) when any photo box is smaller than MIN_PHOTO_BOX_MM on a side."""
        small = min(min(bx[2], bx[3]) for bx in boxes)
        if small >= MIN_PHOTO_BOX_MM:
            return True
        self.issues.append(Issue("NO_ROOM", f"photo box would be {small:.1f} mm on its short side "
                                 f"(minimum {MIN_PHOTO_BOX_MM:.0f} mm); photos not placed. Reduce {why}", page=self.index))
        return False

    @property
    def recto(self):
        return self.index % 2 == 1


def _margins(book: Book, index: int) -> tuple[float, float, float, float]:
    """left, top, right, bottom margins for a page, with binding gutter allowance on the inside."""
    m = book.margins
    inside = float(m["inside"]) + BINDINGS[book.binding]["gutter_extra_mm"]
    outside = float(m["outside"])
    recto = index % 2 == 1
    left, right = (inside, outside) if recto else (outside, inside)
    return left, float(m["top"]), right, float(m["bottom"])


def _cover(box: tuple[float, float, float, float], px: tuple[int, int], focal) -> tuple[float, float, float, float]:
    bx, by, bw, bh = box
    iw, ih = px
    s = max(bw / iw, bh / ih)
    w, h = iw * s, ih * s
    fx, fy = (focal or [0.5, 0.5])
    return bx - (w - bw) * float(fx), by - (h - bh) * float(fy), w, h


def _contain(box: tuple[float, float, float, float], px: tuple[int, int], align=("center", "center")):
    bx, by, bw, bh = box
    iw, ih = px
    s = min(bw / iw, bh / ih)
    w, h = iw * s, ih * s
    ax, ay = align
    x = bx + {"left": 0, "center": (bw - w) / 2, "right": bw - w}[ax]
    y = by + {"top": 0, "center": (bh - h) / 2, "bottom": bh - h}[ay]
    return x, y, w, h


def layout(book: Book, sizes: dict[str, tuple[int, int]]) -> list[PageLayout]:
    """`sizes` maps photo id -> (width_px, height_px) of the source rendition. Geometry depends only on these
    (their aspect ratio); `render` may embed a downsampled file and sets `ImageEl.px` to its size afterwards."""
    W, H, B = book.trim_w, book.trim_h, book.bleed
    full = (-B, -B, W + 2 * B, H + 2 * B)
    cap_lines = line_mm(book, "caption") * CAPTION_LINES
    cap_h = cap_lines + CAPTION_GAP_MM                   # caption lines + gap, in mm
    out: list[PageLayout] = []

    def page_box(index):
        left, t, r, b = _margins(book, index)
        return left, t, W - left - r, H - t - b

    for p in book.pages:
        d = p.data
        if p.span == 1:
            pl = PageLayout(p.index, p.template)
            left, t, bw, bh = page_box(p.index)
            if p.template == "title":
                pl.texts.append(TextEl("title", str(d.get("title", book.title)), left, H * 0.38, bw, 30, "center"))
                if d.get("subtitle"):
                    pl.texts.append(TextEl("subtitle", str(d["subtitle"]), left, H * 0.38 + 16, bw, 12, "center"))
            elif p.template == "text":
                if d.get("heading"):
                    pl.texts.append(TextEl("heading", str(d["heading"]), left, t, bw, 12))
                    pl.texts.append(TextEl("body", str(d.get("text", "")), left, t + 14, bw, bh - 14))
                else:
                    pl.texts.append(TextEl("body", str(d.get("text", "")), left, t, bw, bh))
            elif p.template == "colophon":
                pl.texts.append(TextEl("colophon", str(d.get("text", "")), left, H - float(book.margins["bottom"]) - 40, bw, 40))
            elif p.template == "full-bleed":
                px = sizes[d["photo"]]
                x, y, w, h = _cover(full, px, d.get("focal"))
                pl.images.append(ImageEl(d["photo"], x, y, w, h, full, px))
            elif p.template == "margin":
                px = sizes[d["photo"]]
                caption = str(d.get("caption") or "").strip()
                box = (left, t, bw, bh - (cap_h if caption else 0))
                valign = d.get("valign", "top")
                if pl.fits([box], "the margins or caption_pt"):
                    x, y, w, h = _contain(box, px, ("center", valign))
                    pl.images.append(ImageEl(d["photo"], x, y, w, h, box, px))
                if caption:
                    pl.texts.append(TextEl("caption", caption, left, t + bh - cap_h + CAPTION_GAP_MM, bw, cap_lines,
                                           d.get("caption_align", "left")))
            elif p.template == "pair":
                ids = d["photos"]
                gap = float(d.get("gap_mm", 6))
                a, b = sizes[ids[0]], sizes[ids[1]]
                stacked = d.get("direction", "auto")
                if stacked == "auto":
                    stacked = "column" if (a[0] >= a[1] and b[0] >= b[1]) else "row"
                caption = str(d.get("caption") or "").strip()
                ph = bh - (cap_h if caption else 0)     # photo area; the caption goes below it, inside the margin box
                if stacked == "column":
                    boxes = [(left, t, bw, (ph - gap) / 2), (left, t + (ph + gap) / 2, bw, (ph - gap) / 2)]
                else:
                    boxes = [(left, t, (bw - gap) / 2, ph), (left + (bw + gap) / 2, t, (bw - gap) / 2, ph)]
                if pl.fits(boxes, "gap_mm, the margins or caption_pt"):
                    for pid, px, box in zip(ids, (a, b), boxes, strict=True):
                        x, y, w, h = _contain(box, px)
                        pl.images.append(ImageEl(pid, x, y, w, h, box, px))
                    if caption:
                        # under the photo block (not at the page bottom, where it would merge with the folio),
                        # across the full margin-box width: the box only holds two lines, and a narrow block
                        # (two portraits stacked) would otherwise clip a caption that fits at full width
                        y1 = max(im.y + im.h for im in pl.images)
                        pl.texts.append(TextEl("caption", caption, left, y1 + CAPTION_GAP_MM, bw, cap_lines))
            if book.page_numbers and p.template not in ("title", "blank", "full-bleed"):
                pl.texts.append(TextEl("folio", str(p.index), left, H - float(book.margins["bottom"]) + 6, bw, 6,
                                       "right" if pl.recto else "left"))
            out.append(pl)
        else:
            # spread: p.index is the left (verso) page, p.index + 1 the recto
            px = sizes[d["photo"]]
            left_pl, right_pl = PageLayout(p.index, p.template), PageLayout(p.index + 1, p.template)
            if p.template == "spread-full":
                canvas = (-B, -B, 2 * W + 2 * B, H + 2 * B)
                x, y, w, h = _cover(canvas, px, d.get("focal"))
            else:  # spread-margin
                lo, t, ro, b = _margins(book, p.index)          # verso: left = outside
                box = (lo, t, 2 * W - 2 * lo, H - t - b)
                x, y, w, h = _contain(box, px)
            crossing = x < W < x + w
            left_pl.images.append(ImageEl(d["photo"], x, y, w, h, full, px, crossing, p.index))
            right_pl.images.append(ImageEl(d["photo"], x - W, y, w, h, full, px, crossing, p.index))
            caption = str(d.get("caption") or "").strip()
            if caption and p.template == "spread-margin":
                left, t, bw, bh = page_box(p.index + 1)
                right_pl.texts.append(TextEl("caption", caption, left, H - float(book.margins["bottom"]) + 3, bw, cap_lines, "right"))
            out += [left_pl, right_pl]
    for pl in out:
        for t in pl.texts:
            _whole_lines(book, t, pl)
    return out


MARK_OFFSET_MM = 1.0     # gap between the bleed edge and the start of a crop mark
MARK_LENGTH_MM = 5.0     # crop mark length
MARK_TARGET_R_MM = 1.5   # registration target circle radius
MARK_SLUG_MM = 1.0       # MediaBox margin beyond the marks


@dataclass
class Marks:
    """Crop marks and registration targets outside the bleed, in page mm (origin at the top-left trim
    corner, y down, like every other element). `slug` is how far the MediaBox extends beyond the trim."""
    lines: list[tuple[float, float, float, float]]   # x0, y0, x1, y1
    targets: list[tuple[float, float, float]]        # cx, cy, r (circle plus cross hair)
    slug: float


def crop_marks(book: Book) -> Marks:
    """Crop marks at the four trim corners and a registration target at the middle of each edge. Everything
    starts MARK_OFFSET_MM beyond the bleed edge, so no mark is inside the BleedBox."""
    W, H, B = book.trim_w, book.trim_h, book.bleed
    a = B + MARK_OFFSET_MM            # distance from the trim edge to the start of a mark
    z = a + MARK_LENGTH_MM            # ... and to its end
    lines = []
    for x in (0.0, W):
        sx = -1 if x == 0 else 1      # direction away from the page
        for y in (0.0, H):
            sy = -1 if y == 0 else 1
            lines.append((x + sx * a, y, x + sx * z, y))     # horizontal mark on the trim line y
            lines.append((x, y + sy * a, x, y + sy * z))     # vertical mark on the trim line x
    m = (a + z) / 2
    targets = [(W / 2, -m, MARK_TARGET_R_MM), (W / 2, H + m, MARK_TARGET_R_MM),
               (-m, H / 2, MARK_TARGET_R_MM), (W + m, H / 2, MARK_TARGET_R_MM)]
    return Marks(lines, targets, z + MARK_SLUG_MM)


REPORT_VERSION = 1


def report(book: Book, pages: list[PageLayout]) -> dict:
    """report.json (version 1): placed images with effective resolution, plus structured issues."""
    items, issues = [], list(book.issues)
    seen, flagged = set(), set()
    for pl in pages:
        issues += pl.issues
        for im in pl.images:
            key = (pl.index, im.photo)
            if key in seen:
                continue
            seen.add(key)
            dpi = round(im.dpi)
            level = "ok" if dpi >= 300 else ("low" if dpi >= 200 else "bad")
            items.append({"page": pl.index, "photo": im.photo, "template": pl.template, "placed_mm": [round(im.w, 1), round(im.h, 1)],
                          "px": list(im.px), "dpi": dpi, "level": level, "crossing_gutter": im.crossing_gutter})
            # images[] keeps one entry per page, but a spread photo is one placement: its issues are raised
            # once, on the verso page of the spread
            placement = (im.spread if im.spread is not None else pl.index, im.photo)
            if placement in flagged:
                continue
            flagged.add(placement)
            if level != "ok":
                issues.append(Issue("BAD_DPI" if level == "bad" else "LOW_DPI",
                                    f"{dpi} dpi at {im.w:.0f}×{im.h:.0f} mm", page=pl.index, photo=im.photo))
            if im.crossing_gutter and not BINDINGS[book.binding]["crossing_ok"]:
                issues.append(Issue("GUTTER_CROSSING", f"spread image crosses the gutter; {book.binding} binding hides ~6-8 mm at the spine",
                                    page=pl.index, photo=im.photo))
    return {"version": REPORT_VERSION, "title": book.title, "pages": book.page_count,
            "trim_mm": [book.trim_w, book.trim_h], "bleed_mm": book.bleed, "binding": book.binding,
            "images": items, "issues": [i.to_dict() for i in issues]}
