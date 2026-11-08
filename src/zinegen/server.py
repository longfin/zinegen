"""MCP server exposing the zinegen pipeline to any agent (Claude Code, Codex, ...)."""
from __future__ import annotations

import json
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer

from . import presets, spec

mcp = MCPServer(
    "zinegen",
    instructions=(
        "Typeset a photobook from a Lightroom album into a print-ready PDF/X. Workflow: zg_list_albums -> "
        "zg_init_book (draft book.yaml) -> edit book.yaml (sequence, templates, captions) -> zg_fetch -> "
        "zg_render -> zg_proof (look at the spreads) -> iterate -> zg_check. The book.yaml is the single "
        "source of truth; keep edits there. Use zg_spec_help for the template reference."
    ),
)


def _book(path: str) -> spec.Book:
    p = Path(path).expanduser()
    return spec.load(p / "book.yaml" if p.is_dir() else p)


@mcp.tool()
def zg_spec_help() -> str:
    """Reference for book.yaml: trim presets, bindings, page templates and their fields."""
    lines = ["# trim presets"]
    lines += [f"{k}: {w}×{h} mm ({n})" for k, (w, h, n) in presets.TRIM_PRESETS.items()]
    lines += ["", "# bindings"]
    lines += [f"{k}: {b['note']} (pages multiple of {b['page_multiple']})" for k, b in presets.BINDINGS.items()]
    lines += ["", "# page templates (fields)"]
    fields = {
        "title": "title, subtitle", "blank": "-", "text": "heading?, text", "colophon": "text",
        "full-bleed": "photo, focal [x,y] 0..1", "margin": "photo, caption?, valign top|center|bottom, caption_align",
        "pair": "photos [a,b], direction auto|row|column, gap_mm, caption?",
        "spread-full": "photo, focal (occupies 2 pages, starts on a left page)",
        "spread-margin": "photo, caption? (occupies 2 pages)",
    }
    lines += [f"{k}: {spec.TEMPLATES[k][1]} — {fields[k]}" for k in spec.TEMPLATES]
    lines += ["every page item may carry `note` (free text, ignored by the renderer)"]
    lines += ["", "# top-level keys (book.yaml must start with `version: 1`; unknown keys are rejected)", "title, trim {preset|width_mm,height_mm}, bleed_mm, binding, paper {caliper_um}, "
              "margins_mm {top,bottom,inside,outside}, fonts {body,caption,title}, type {body_pt,caption_pt,title_pt}, "
              "page_numbers, marks, output {standard pdf/x-1a|pdf/x-4, icc, intent, jpeg_quality}, source {album_id,size}, pages[]"]
    return "\n".join(lines)


@mcp.tool()
def zg_list_albums(query: str | None = None) -> str:
    """Lightroom albums (id, name, sort mode). `query` filters by name substring."""
    from lr_mcp import client as lr
    q = (query or "").lower()
    rows = []
    for alb in lr.paginate(lr.catalog_path() + "albums?limit=500"):
        if alb.get("subtype") != "collection":
            continue
        name = alb.get("payload", {}).get("name", "")
        if q and q not in name.lower():
            continue
        rows.append(f"{alb['id']} | {name} | {alb['payload'].get('assetSortOrder')}")
    return "\n".join(rows) or "no albums"


@mcp.tool()
def zg_album_photos(album_id: str) -> str:
    """Photos of an album in the album's own order, with title/caption and cropped size. One line per photo."""
    from .lr import compat
    info = compat.album_info(album_id)
    out = [f"{info['name']} (order: {info['sort']})"]
    for p in compat.album_assets(album_id):
        size = p.cropped or p.original
        orient = "L" if size and size[0] >= size[1] else "P"
        bits = [p.id, p.file or "?", (p.captured or "?")[:16], f"{size[0]}x{size[1]}{orient}" if size else "?"]
        if p.rating:
            bits.append("★" * p.rating)
        if p.flag and p.flag != "unflagged":
            bits.append(p.flag)
        if p.title:
            bits.append(f"title={p.title!r}")
        if p.caption:
            bits.append(f"caption={p.caption!r}")
        out.append(" | ".join(bits))
    return "\n".join(out)


@mcp.tool()
def zg_init_book(album_id: str, dir: str | None = None, standard: str = "pdf/x-1a", icc: str | None = None,
                 force: bool = False) -> str:
    """Draft a book.yaml for an album under `dir` (default books/<album name>). Returns the path."""
    import argparse

    from .cli import cmd_init
    rc = cmd_init(argparse.Namespace(album=album_id, dir=dir, standard=standard, icc=icc, force=force))
    if rc:
        return "book.yaml already exists; pass force=true to overwrite"
    name = dir or "books/<album>"
    return f"drafted {name}/book.yaml; read it, then edit sequence/templates/captions and call zg_fetch + zg_render"


@mcp.tool()
def zg_fetch(book: str, force: bool = False) -> str:
    """Download (and cache) the renditions referenced by book.yaml."""
    from .lr import compat
    b = _book(book)
    photos = [p for p in compat.album_assets(b.source["album_id"]) if p.id in set(b.photo_ids())]
    res = compat.fetch_renditions(photos, b.cache_dir, b.source.get("size", "fullsize"), force)
    return json.dumps(res, ensure_ascii=False)


@mcp.tool()
def zg_render(book: str) -> str:
    """Render book.yaml to out/interior.pdf (PDF/X) and out/report.json. Returns the report summary."""
    from . import render
    b = _book(book)
    rep = render.render(b)
    return json.dumps({"pdf": rep["pdf"], "pages": rep["pages"], "issues": rep["issues"]}, ensure_ascii=False)


@mcp.tool()
def zg_proof(book: str, spreads: list[int] | None = None, dpi: int = 40) -> list:
    """Render spread proofs and return them as images (all spreads, or the given 1-based numbers; max 12)."""
    from . import proof
    b = _book(book)
    files = proof.proof(b, dpi)
    if spreads:
        files = [files[i - 1] for i in spreads if 0 < i <= len(files)]
    files = files[:12]
    out = [f"{len(files)} spreads (dir {b.proof_dir})"]
    for f in files:
        out.append(Image(data=f.read_bytes(), format="png"))
    return out


@mcp.tool()
def zg_check(book: str) -> str:
    """Structural PDF/X check of out/interior.pdf (boxes, output intent, colour spaces, fonts, resolution)."""
    from . import check
    return json.dumps(check.check(_book(book)), indent=1, ensure_ascii=False)


@mcp.tool()
def zg_spine(pages: int, caliper_um: float, binding: str = "perfect", board_mm: float = 2.0) -> str:
    """Spine width in mm from page count and paper caliper (µm). Hardcover adds boards."""
    return f"{presets.spine_mm(pages, caliper_um, binding, board_mm)} mm (pages rounded to {presets.round_up_pages(pages, binding)} for {binding})"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
