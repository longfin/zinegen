"""Book spec (book.yaml): load, validate, normalise. The agent edits this file; everything else is derived."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .presets import BINDINGS, resolve_trim

TEMPLATES = {
    # name: (pages occupied, needs photo(s), description)
    "title": (1, 0, "제목 페이지"),
    "blank": (1, 0, "빈 페이지"),
    "text": (1, 0, "본문 텍스트 페이지(서문, 에세이)"),
    "colophon": (1, 0, "판권면"),
    "full-bleed": (1, 1, "사진 한 장 전면 재단"),
    "margin": (1, 1, "사진 한 장을 여백 안에 맞춤 + 캡션"),
    "pair": (1, 2, "사진 두 장을 한 페이지에 나란히(가로 또는 세로 배치)"),
    "spread-full": (2, 1, "사진 한 장을 펼침 전면 재단(왼쪽 페이지에서 시작)"),
    "spread-margin": (2, 1, "사진 한 장을 펼침 여백 안에(왼쪽 페이지에서 시작)"),
}

DEFAULTS = {
    "bleed_mm": 3,
    "binding": "perfect",
    "margins_mm": {"top": 14, "bottom": 16, "inside": 16, "outside": 12},
    "fonts": {"body": "Nanum Myeongjo", "caption": "Nanum Gothic", "title": "Nanum Myeongjo"},
    "type": {"body_pt": 10, "caption_pt": 7.5, "title_pt": 24},
    "page_numbers": False,
    "output": {"standard": "pdf/x-1a", "intent": "perceptual", "icc": None, "jpeg_quality": 95},
    "source": {"size": "fullsize"},
}


@dataclass
class Page:
    index: int                 # 1-based page number of its first page
    template: str
    data: dict
    span: int = 1

    @property
    def recto(self) -> bool:
        return self.index % 2 == 1


@dataclass
class Book:
    path: Path
    raw: dict
    trim_w: float
    trim_h: float
    bleed: float
    binding: str
    margins: dict
    fonts: dict
    type: dict
    output: dict
    source: dict
    pages: list[Page] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def title(self):
        return self.raw.get("title") or self.path.parent.name

    @property
    def page_count(self):
        return sum(p.span for p in self.pages)

    @property
    def root(self) -> Path:
        return self.path.parent

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def out_dir(self) -> Path:
        return self.root / "out"

    @property
    def proof_dir(self) -> Path:
        return self.root / "proof"

    def photo_ids(self) -> list[str]:
        ids = []
        for p in self.pages:
            for k in ("photo", "photos"):
                v = p.data.get(k)
                if isinstance(v, str):
                    ids.append(v)
                elif isinstance(v, list):
                    ids += [x for x in v if isinstance(x, str)]
        return ids


def _merge(base: dict, over: dict | None) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | Path) -> Book:
    path = Path(path).resolve()
    raw = yaml.safe_load(path.read_text()) or {}
    cfg = _merge(DEFAULTS, raw)
    w, h = resolve_trim(cfg.get("trim") or {"preset": "A5"})
    binding = cfg["binding"]
    if binding not in BINDINGS:
        raise ValueError(f"unknown binding {binding!r}; known: {', '.join(BINDINGS)}")
    book = Book(path=path, raw=raw, trim_w=w, trim_h=h, bleed=float(cfg["bleed_mm"]), binding=binding,
                margins=cfg["margins_mm"], fonts=cfg["fonts"], type=cfg["type"], output=cfg["output"],
                source=cfg.get("source") or {})
    # layout pages: spreads must start on a verso (even) page; insert blanks when needed
    index = 1
    for i, item in enumerate(raw.get("pages") or []):
        if isinstance(item, str):
            item = {"template": item}
        tpl = item.get("template", "margin")
        if tpl not in TEMPLATES:
            raise ValueError(f"pages[{i}]: unknown template {tpl!r}; known: {', '.join(TEMPLATES)}")
        span, nphotos, _ = TEMPLATES[tpl]
        have = item.get("photos") if nphotos == 2 else item.get("photo")
        if nphotos == 1 and not isinstance(have, str):
            raise ValueError(f"pages[{i}] ({tpl}): needs `photo: <asset id>`")
        if nphotos == 2 and not (isinstance(have, list) and len(have) == 2):
            raise ValueError(f"pages[{i}] ({tpl}): needs `photos: [<asset id>, <asset id>]`")
        if span == 2 and index % 2 == 1:
            book.pages.append(Page(index=index, template="blank", data={"auto": True}))
            book.warnings.append(f"pages[{i}] ({tpl}) moved to page {index + 1}: a blank page was inserted so the spread starts on a left page")
            index += 1
        book.pages.append(Page(index=index, template=tpl, data=item, span=span))
        index += span
    # pad to the binding's page multiple with trailing blanks
    from .presets import round_up_pages
    target = round_up_pages(book.page_count, binding)
    if target != book.page_count:
        book.warnings.append(f"{book.page_count} pages padded to {target} with blank pages ({binding} binding needs a multiple of {BINDINGS[binding]['page_multiple']})")
        while book.page_count < target:
            book.pages.append(Page(index=index, template="blank", data={"auto": True}))
            index += 1
    return book


def skeleton(album: dict, photos: list, out_standard: str = "pdf/x-1a", icc: str | None = None) -> dict:
    """A first draft spec for an album: title page, one photo per page alternating templates, colophon."""
    pages: list[dict] = [{"template": "title", "title": album.get("name") or "Untitled", "subtitle": ""},
                         {"template": "blank"}]
    for i, p in enumerate(photos):
        w, h = (p.cropped or p.original or (3, 2))
        landscape = w >= h
        if i % 5 == 4 and landscape:
            pages.append({"template": "spread-full", "photo": p.id, "focal": [0.5, 0.5], "_file": p.file})
        elif landscape and i % 3 == 0:
            pages.append({"template": "full-bleed", "photo": p.id, "focal": [0.5, 0.5], "_file": p.file})
        else:
            pages.append({"template": "margin", "photo": p.id, "caption": p.caption or "", "_file": p.file})
    pages.append({"template": "colophon", "text": f"{album.get('name', '')}\n\n© {album.get('owner', '')}"})
    return {
        "title": album.get("name") or "Untitled",
        "trim": {"preset": "A5"},
        "bleed_mm": 3,
        "binding": "perfect",
        "paper": {"caliper_um": 110},
        "margins_mm": dict(DEFAULTS["margins_mm"]),
        "fonts": dict(DEFAULTS["fonts"]),
        "output": {"standard": out_standard, "icc": icc, "intent": "perceptual"},
        "source": {"album_id": album["id"], "size": "fullsize"},
        "pages": pages,
    }


def dump(spec: dict, path: Path):
    path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=100))
