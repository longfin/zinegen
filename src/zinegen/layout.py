"""Deterministic page geometry: turns the book spec into absolutely positioned elements (mm).

Every image placement is computed here (not by CSS object-fit) so that effective resolution,
bleed coverage and gutter crossing can be reported exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .issues import Issue
from .presets import BINDINGS
from .spec import Book, Page


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
    """`sizes` maps photo id -> (width_px, height_px) of the file that will be embedded."""
    W, H, B = book.trim_w, book.trim_h, book.bleed
    full = (-B, -B, W + 2 * B, H + 2 * B)
    cap_pt = float(book.type["caption_pt"])
    cap_h = cap_pt * 0.3528 * 1.5 * 2 + 2   # two caption lines + gap, in mm
    out: list[PageLayout] = []

    def page_box(index):
        l, t, r, b = _margins(book, index)
        return l, t, W - l - r, H - t - b

    for p in book.pages:
        d = p.data
        if p.span == 1:
            pl = PageLayout(p.index, p.template)
            l, t, bw, bh = page_box(p.index)
            if p.template == "title":
                pl.texts.append(TextEl("title", str(d.get("title", book.title)), l, H * 0.38, bw, 30, "center"))
                if d.get("subtitle"):
                    pl.texts.append(TextEl("subtitle", str(d["subtitle"]), l, H * 0.38 + 16, bw, 12, "center"))
            elif p.template == "text":
                if d.get("heading"):
                    pl.texts.append(TextEl("heading", str(d["heading"]), l, t, bw, 12))
                    pl.texts.append(TextEl("body", str(d.get("text", "")), l, t + 14, bw, bh - 14))
                else:
                    pl.texts.append(TextEl("body", str(d.get("text", "")), l, t, bw, bh))
            elif p.template == "colophon":
                pl.texts.append(TextEl("colophon", str(d.get("text", "")), l, H - float(book.margins["bottom"]) - 40, bw, 40))
            elif p.template == "full-bleed":
                px = sizes[d["photo"]]
                x, y, w, h = _cover(full, px, d.get("focal"))
                pl.images.append(ImageEl(d["photo"], x, y, w, h, full, px))
            elif p.template == "margin":
                px = sizes[d["photo"]]
                caption = str(d.get("caption") or "").strip()
                box = (l, t, bw, bh - (cap_h if caption else 0))
                valign = d.get("valign", "top")
                x, y, w, h = _contain(box, px, ("center", valign))
                pl.images.append(ImageEl(d["photo"], x, y, w, h, box, px))
                if caption:
                    pl.texts.append(TextEl("caption", caption, l, t + bh - cap_h + 2, bw, cap_h, d.get("caption_align", "left")))
            elif p.template == "pair":
                ids = d["photos"]
                gap = float(d.get("gap_mm", 6))
                a, b = sizes[ids[0]], sizes[ids[1]]
                stacked = d.get("direction", "auto")
                if stacked == "auto":
                    stacked = "column" if (a[0] >= a[1] and b[0] >= b[1]) else "row"
                if stacked == "column":
                    boxes = [(l, t, bw, (bh - gap) / 2), (l, t + (bh + gap) / 2, bw, (bh - gap) / 2)]
                else:
                    boxes = [(l, t, (bw - gap) / 2, bh), (l + (bw + gap) / 2, t, (bw - gap) / 2, bh)]
                for pid, px, box in zip(ids, (a, b), boxes):
                    x, y, w, h = _contain(box, px)
                    pl.images.append(ImageEl(pid, x, y, w, h, box, px))
                caption = str(d.get("caption") or "").strip()
                if caption:
                    pl.texts.append(TextEl("caption", caption, l, H - float(book.margins["bottom"]) + 3, bw, cap_h))
            if book.page_numbers and p.template not in ("title", "blank", "full-bleed"):
                pl.texts.append(TextEl("folio", str(p.index), l, H - float(book.margins["bottom"]) + 6, bw, 6,
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
            left_pl.images.append(ImageEl(d["photo"], x, y, w, h, full, px, crossing))
            right_pl.images.append(ImageEl(d["photo"], x - W, y, w, h, full, px, crossing))
            caption = str(d.get("caption") or "").strip()
            if caption and p.template == "spread-margin":
                l, t, bw, bh = page_box(p.index + 1)
                right_pl.texts.append(TextEl("caption", caption, l, H - float(book.margins["bottom"]) + 3, bw, cap_h, "right"))
            out += [left_pl, right_pl]
    return out


REPORT_VERSION = 1


def report(book: Book, pages: list[PageLayout]) -> dict:
    """report.json (version 1): placed images with effective resolution, plus structured issues."""
    items, issues = [], list(book.issues)
    seen = set()
    for pl in pages:
        for im in pl.images:
            key = (pl.index, im.photo)
            if key in seen:
                continue
            seen.add(key)
            dpi = round(im.dpi)
            level = "ok" if dpi >= 300 else ("low" if dpi >= 200 else "bad")
            items.append({"page": pl.index, "photo": im.photo, "template": pl.template, "placed_mm": [round(im.w, 1), round(im.h, 1)],
                          "px": list(im.px), "dpi": dpi, "level": level, "crossing_gutter": im.crossing_gutter})
            if level != "ok":
                issues.append(Issue("BAD_DPI" if level == "bad" else "LOW_DPI",
                                    f"{dpi} dpi at {im.w:.0f}×{im.h:.0f} mm", page=pl.index, photo=im.photo))
            if im.crossing_gutter and not BINDINGS[book.binding]["crossing_ok"]:
                issues.append(Issue("GUTTER_CROSSING", f"spread image crosses the gutter; {book.binding} binding hides ~6-8 mm at the spine",
                                    page=pl.index, photo=im.photo))
    return {"version": REPORT_VERSION, "title": book.title, "pages": book.page_count,
            "trim_mm": [book.trim_w, book.trim_h], "bleed_mm": book.bleed, "binding": book.binding,
            "images": items, "issues": [i.to_dict() for i in issues]}
