"""Trim-size presets, binding rules and spine calculation. All lengths in millimetres."""
from __future__ import annotations

# name -> (width, height, note)
TRIM_PRESETS: dict[str, tuple[float, float, str]] = {
    "A4": (210, 297, "ISO A4 portrait"),
    "A4L": (297, 210, "ISO A4 landscape"),
    "A5": (148, 210, "ISO A5 portrait"),
    "A5L": (210, 148, "ISO A5 landscape"),
    "B5": (176, 250, "ISO B5 portrait"),
    "B5K": (182, 257, "JIS B5 (국내 B5 단행본)"),
    "SQ210": (210, 210, "square 210"),
    "SQ180": (180, 180, "square 180"),
    "46": (127, 188, "46판 (국내 단행본)"),
    "SIN46": (128, 188, "신국판"),
    "CROWN": (152, 225, "크라운판"),
    "8x10": (203.2, 254, "8×10 in portrait"),
    "10x8": (254, 203.2, "10×8 in landscape"),
    "7x7": (177.8, 177.8, "7×7 in square"),
}

BINDINGS = {
    # extra inside margin on top of the base inside margin, and whether spreads can cross the gutter safely
    "saddle": {"gutter_extra_mm": 0, "crossing_ok": True, "page_multiple": 4,
               "note": "중철. 페이지 수는 4의 배수. 펼침 사진에 유리"},
    "perfect": {"gutter_extra_mm": 4, "crossing_ok": False, "page_multiple": 2,
                "note": "무선. 책등 쪽 6~8mm는 잘 안 보임. 펼침 사진은 중앙에 중요한 피사체 금지"},
    "hardcover": {"gutter_extra_mm": 3, "crossing_ok": False, "page_multiple": 16,
                  "note": "양장(사철). 대수는 보통 16쪽 배수"},
    "layflat": {"gutter_extra_mm": 0, "crossing_ok": True, "page_multiple": 2,
                "note": "레이플랫(PUR/합지). 펼침 사진에 최적"},
}


def resolve_trim(trim: dict | str) -> tuple[float, float]:
    """`{"preset": "A5"}`, `"A5"`, or `{"width_mm": 148, "height_mm": 210}`."""
    if isinstance(trim, str):
        trim = {"preset": trim}
    if "preset" in trim:
        key = str(trim["preset"]).upper()
        if key not in TRIM_PRESETS:
            raise ValueError(f"unknown trim preset {trim['preset']!r}; known: {', '.join(TRIM_PRESETS)}")
        w, h, _ = TRIM_PRESETS[key]
        return float(w), float(h)
    try:
        return float(trim["width_mm"]), float(trim["height_mm"])
    except KeyError:
        raise ValueError("trim needs either `preset` or `width_mm` + `height_mm`") from None


def spine_mm(pages: int, caliper_um: float, binding: str = "perfect", cover_board_mm: float = 0.0) -> float:
    """Spine width. Perfect/saddle: sheets × caliper. Hardcover adds two boards + 2 mm hinge allowance."""
    sheets = pages / 2
    base = sheets * caliper_um / 1000
    if binding == "hardcover":
        return round(base + 2 * cover_board_mm + 2.0, 2)
    if binding == "saddle":
        return 0.0
    return round(base, 2)


def round_up_pages(pages: int, binding: str) -> int:
    m = BINDINGS[binding]["page_multiple"]
    return pages if pages % m == 0 else pages + (m - pages % m)
