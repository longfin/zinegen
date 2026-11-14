"""Book spec (book.yaml): load, validate against the JSON Schema, normalise.

The agent edits this file; everything else is derived. Format version 1 (`version: 1`), schema in
`schema/book.schema.json`. Unknown keys are errors so that typos never silently disappear.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

import jsonschema
import yaml

from .issues import Issue
from .presets import BINDINGS, resolve_trim, round_up_pages

SPEC_VERSION = 1
SCHEMA = json.loads((files("zinegen") / "schema" / "book.schema.json").read_text())

TEMPLATES = {
    # name: (pages occupied, description)
    "title": (1, "제목 페이지"),
    "blank": (1, "빈 페이지"),
    "text": (1, "본문 텍스트 페이지(서문, 에세이)"),
    "colophon": (1, "판권면"),
    "full-bleed": (1, "사진 한 장 전면 재단"),
    "margin": (1, "사진 한 장을 여백 안에 맞춤 + 캡션"),
    "pair": (1, "사진 두 장을 한 페이지에 나란히(가로 또는 세로 배치)"),
    "spread-full": (2, "사진 한 장을 펼침 전면 재단(왼쪽 페이지에서 시작)"),
    "spread-margin": (2, "사진 한 장을 펼침 여백 안에(왼쪽 페이지에서 시작)"),
}

DEFAULT_MAX_DPI = 350   # PDF/X-1a images are downsampled to this effective resolution at their placed size

DEFAULTS = {
    "bleed_mm": 3,
    "binding": "perfect",
    "margins_mm": {"top": 14, "bottom": 16, "inside": 16, "outside": 12},
    "fonts": {"body": "Nanum Myeongjo", "caption": "Nanum Gothic", "title": "Nanum Myeongjo"},
    "type": {"body_pt": 10, "caption_pt": 7.5, "title_pt": 24},
    "page_numbers": False,
    "marks": True,
    "output": {"standard": "pdf/x-1a", "intent": "perceptual", "icc": None, "jpeg_quality": 95,
               "max_dpi": DEFAULT_MAX_DPI},
    "source": {"size": "fullsize"},
}


class SpecError(ValueError):
    pass


@dataclass
class Page:
    index: int                 # 1-based page number of its first page
    template: str
    data: dict
    span: int = 1
    source: int | None = None  # index into book.yaml `pages` (None for auto-inserted blanks)

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
    marks: bool = True
    page_numbers: bool = False
    pages: list[Page] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

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

    @property
    def missing_file(self) -> Path:
        """Photo ids the last fetch could not find in Lightroom (cache/missing.json), so that render can say so
        instead of asking for another fetch."""
        return self.cache_dir / "missing.json"

    def missing_ids(self) -> set[str]:
        try:
            return set(json.loads(self.missing_file.read_text()))
        except (OSError, ValueError, TypeError):
            return set()

    def photo_ids(self) -> list[str]:
        ids = []
        for p in self.pages:
            v = p.data.get("photo")
            if isinstance(v, str):
                ids.append(v)
            ids += list(p.data.get("photos") or [])
        return list(dict.fromkeys(ids))


def record_missing(book: Book, missing: list[str]):
    """Remember (or forget) the ids a fetch did not find in Lightroom; see Book.missing_file."""
    if missing:
        book.cache_dir.mkdir(parents=True, exist_ok=True)
        book.missing_file.write_text(json.dumps(sorted(missing)))
    else:
        book.missing_file.unlink(missing_ok=True)


def _merge(base: dict, over: dict | None) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _path(err: jsonschema.ValidationError) -> str:
    parts = []
    for p in err.absolute_path:
        parts.append(f"[{p}]" if isinstance(p, int) else ("." if parts else "") + str(p))
    return "".join(parts) or "<root>"


def validate(raw: dict) -> list[str]:
    """Schema errors as human/agent readable strings. Empty list means valid."""
    if raw.get("version") != SPEC_VERSION:
        return [f"version: must be {SPEC_VERSION} (add `version: {SPEC_VERSION}` at the top of book.yaml)"]
    v = jsonschema.Draft202012Validator(SCHEMA)
    errors = []
    for err in sorted(v.iter_errors(raw), key=lambda e: [str(x) for x in e.absolute_path]):
        if err.validator == "oneOf" and list(err.absolute_path) == ["trim"]:
            errors.append("trim: use a preset name, {preset: NAME} or {width_mm, height_mm}")
        else:
            errors.append(f"{_path(err)}: {err.message}")
    if errors:
        return errors
    # per-template validation with the matching sub-schema (precise messages, no oneOf noise)
    for i, item in enumerate(raw.get("pages") or []):
        tpl = item["template"]
        sub = {"$defs": SCHEMA["$defs"], **SCHEMA["$defs"]["templates"][tpl]}
        for err in jsonschema.Draft202012Validator(sub).iter_errors(item):
            where = _path(err)
            errors.append(f"pages[{i}] ({tpl}){'' if where == '<root>' else ' ' + where}: {err.message}")
    return errors


def load(path: str | Path) -> Book:
    path = Path(path).resolve()
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise SpecError(f"{path.name}: top level must be a mapping")
    errors = validate(raw)
    if errors:
        raise SpecError(f"{path.name} is not a valid book spec:\n  " + "\n  ".join(errors))
    cfg = _merge(DEFAULTS, raw)
    w, h = resolve_trim(cfg.get("trim") or {"preset": "A5"})
    binding = cfg["binding"]
    book = Book(path=path, raw=raw, trim_w=w, trim_h=h, bleed=float(cfg["bleed_mm"]), binding=binding,
                margins=cfg["margins_mm"], fonts=cfg["fonts"], type=cfg["type"], output=cfg["output"],
                source=cfg.get("source") or {}, marks=bool(cfg["marks"]), page_numbers=bool(cfg["page_numbers"]))
    index = 1
    for i, item in enumerate(raw.get("pages") or []):
        tpl = item["template"]
        span, _ = TEMPLATES[tpl]
        if span == 2 and index % 2 == 1:
            book.pages.append(Page(index=index, template="blank", data={}, source=None))
            book.issues.append(Issue("SPREAD_MOVED", f"pages[{i}] ({tpl}) starts on page {index + 1}; a blank page was inserted so the spread begins on a left page", page=index))
            index += 1
        book.pages.append(Page(index=index, template=tpl, data=item, span=span, source=i))
        index += span
    target = round_up_pages(book.page_count, binding)
    if target != book.page_count:
        book.issues.append(Issue("PAGES_PADDED", f"{book.page_count} pages padded to {target} with blank pages ({binding} binding needs a multiple of {BINDINGS[binding]['page_multiple']})"))
        while book.page_count < target:
            book.pages.append(Page(index=index, template="blank", data={}, source=None))
            index += 1
    return book


def skeleton(album: dict, photos: list, out_standard: str = "pdf/x-1a", icc: str | None = None) -> dict:
    """A first draft spec for an album: title page, one photo per page alternating templates, colophon."""
    pages: list[dict] = [{"template": "title", "title": album.get("name") or "Untitled", "subtitle": ""},
                         {"template": "blank"}]
    for i, p in enumerate(photos):
        w, h = (p.cropped or p.original or (3, 2))
        landscape = w >= h
        note = p.file or ""
        if i % 5 == 4 and landscape:
            pages.append({"template": "spread-full", "photo": p.id, "focal": [0.5, 0.5], "note": note})
        elif landscape and i % 3 == 0:
            pages.append({"template": "full-bleed", "photo": p.id, "focal": [0.5, 0.5], "note": note})
        else:
            pages.append({"template": "margin", "photo": p.id, "caption": p.caption or "", "note": note})
    pages.append({"template": "colophon", "text": f"{album.get('name', '')}\n"})
    return {
        "version": SPEC_VERSION,
        "title": album.get("name") or "Untitled",
        "trim": {"preset": "A5"},
        "bleed_mm": 3,
        "binding": "perfect",
        "margins_mm": dict(DEFAULTS["margins_mm"]),
        "fonts": dict(DEFAULTS["fonts"]),
        "output": {"standard": out_standard, "icc": icc, "intent": "perceptual"},
        "source": {"album_id": album["id"], "size": "fullsize"},
        "pages": pages,
    }


def dump(spec: dict, path: Path):
    path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=100))
