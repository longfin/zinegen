"""zinegen command line: doctor | presets | albums | init | fetch | render | proof | check | build | spine | mcp"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

from . import presets, spec


def log(msg):
    print(msg, file=sys.stderr)


def cmd_doctor(_):
    ok = True

    def line(good, text):
        nonlocal ok
        ok &= bool(good)
        print(("[ok] " if good else "[!!] ") + text)

    line(shutil.which("gs"), "Ghostscript (gs) for proofs" + ("" if shutil.which("gs") else " — brew install ghostscript"))
    line(shutil.which("fc-list"), "fontconfig (fc-list) for font lookup")
    from .render import _setup_dyld
    _setup_dyld()
    try:
        import weasyprint  # noqa: F401
        line(True, f"WeasyPrint {weasyprint.__version__} (pango found)")
    except OSError as e:
        line(False, f"WeasyPrint cannot load pango: {str(e).splitlines()[0]} — brew install pango")
    from . import color
    adobe = [p for p in ("JapanColor2001Coated", "CoatedFOGRA39", "USWebCoatedSWOP") if color.find_profile(p)]
    line(adobe, "CMYK ICC profiles found: " + (", ".join(adobe) if adobe else "none (set output.icc to a profile path)"))
    try:
        from lr_mcp import client as lr
        lr.catalog_path()
        line(True, "Lightroom (lr-mcp) signed in")
    except Exception as e:  # noqa: BLE001
        line(False, f"Lightroom: {e}")
    print("Setup is complete." if ok else "Fix the items marked [!!].")
    return 0 if ok else 1


def cmd_presets(_):
    for k, (w, h, note) in presets.TRIM_PRESETS.items():
        print(f"{k:8} {w:>6} × {h:<6} mm  {note}")
    print()
    for k, b in presets.BINDINGS.items():
        print(f"{k:10} pages ×{b['page_multiple']}, gutter +{b['gutter_extra_mm']}mm, crossing {'ok' if b['crossing_ok'] else 'avoid'}: {b['note']}")


def cmd_albums(a):
    from lr_mcp import client as lr
    q = (a.query or "").lower()
    cat = lr.catalog_path()
    for alb in lr.paginate(cat + "albums?limit=500"):
        if alb.get("subtype") != "collection":
            continue
        name = alb.get("payload", {}).get("name", "")
        if q and q not in name.lower():
            continue
        print(f"{alb['id']}  {name}  [{alb['payload'].get('assetSortOrder')}]")


def _book(path) -> spec.Book:
    p = Path(path)
    if p.is_dir():
        p = p / "book.yaml"
    return spec.load(p)


def cmd_init(a):
    from .lr import compat
    info = compat.album_info(a.album)
    photos = compat.album_assets(a.album)
    name = re.sub(r"[^\w가-힣-]+", "_", info["name"] or a.album).strip("_")
    root = Path(a.dir) if a.dir else Path("books") / name
    root.mkdir(parents=True, exist_ok=True)
    out = root / "book.yaml"
    if out.exists() and not a.force:
        log(f"{out} exists; use --force to overwrite")
        return 1
    from . import color
    icc = a.icc or next((p for p in ("JapanColor2001Coated", "CoatedFOGRA39") if color.find_profile(p)), None)
    s = spec.skeleton(info, photos, a.standard, icc)
    spec.dump(s, out)
    (root / "photos.json").write_text(json.dumps([p.to_dict() for p in photos], indent=1, ensure_ascii=False))
    log(f"wrote {out} with {len(photos)} photos (album order: {info['sort']}); photo metadata in {root / 'photos.json'}")
    print(out)
    return 0


def cmd_fetch(a):
    from .lr import compat
    book = _book(a.book)
    album = book.source.get("album_id")
    photos = compat.album_assets(album) if album else []
    wanted = set(book.photo_ids())
    photos = [p for p in photos if p.id in wanted] if album else []
    missing = wanted - {p.id for p in photos}
    if missing:
        # photos referenced outside the album: fetch by id
        from lr_mcp import client as lr
        ids = ",".join(sorted(missing))
        for asset in lr.get_json(lr.catalog_path() + f"assets?asset_ids={ids}").get("resources", []):
            photos.append(compat._photo(asset))
    res = compat.fetch_renditions(photos, book.cache_dir, book.source.get("size", "fullsize"), a.force, log=log)
    log(json.dumps(res, ensure_ascii=False))
    return 0 if not res["errors"] else 1


def cmd_render(a):
    from . import render
    book = _book(a.book)
    rep = render.render(book, log=log)
    for w in rep["warnings"]:
        log("warning: " + w)
    print(rep["pdf"])
    return 0


def cmd_proof(a):
    from . import proof
    book = _book(a.book)
    for p in proof.proof(book, a.dpi, log=log):
        print(p)
    return 0


def cmd_check(a):
    from . import check
    res = check.check(_book(a.book))
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res["ok"] else 1


def cmd_build(a):
    rc = cmd_fetch(a)
    if rc:
        return rc
    cmd_render(a)
    cmd_proof(a)
    return cmd_check(a)


def cmd_spine(a):
    print(f"{presets.spine_mm(a.pages, a.caliper, a.binding, a.board)} mm")


def cmd_mcp(_):
    from .server import main as serve
    serve()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="zinegen", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(f=cmd_doctor)
    sub.add_parser("presets").set_defaults(f=cmd_presets)
    p = sub.add_parser("albums"); p.add_argument("query", nargs="?"); p.set_defaults(f=cmd_albums)
    p = sub.add_parser("init", help="draft book.yaml from a Lightroom album")
    p.add_argument("--album", required=True); p.add_argument("--dir"); p.add_argument("--standard", default="pdf/x-1a", choices=["pdf/x-1a", "pdf/x-4"])
    p.add_argument("--icc"); p.add_argument("--force", action="store_true"); p.set_defaults(f=cmd_init)
    for name, f in (("fetch", cmd_fetch), ("render", cmd_render), ("check", cmd_check), ("build", cmd_build)):
        p = sub.add_parser(name); p.add_argument("book", nargs="?", default="book.yaml"); p.add_argument("--force", action="store_true")
        p.add_argument("--dpi", type=int, default=50); p.set_defaults(f=f)
    p = sub.add_parser("proof"); p.add_argument("book", nargs="?", default="book.yaml"); p.add_argument("--dpi", type=int, default=50); p.set_defaults(f=cmd_proof)
    p = sub.add_parser("spine"); p.add_argument("--pages", type=int, required=True); p.add_argument("--caliper", type=float, required=True, help="paper caliper in µm")
    p.add_argument("--binding", default="perfect", choices=list(presets.BINDINGS)); p.add_argument("--board", type=float, default=2.0); p.set_defaults(f=cmd_spine)
    sub.add_parser("mcp", help="run the MCP server (stdio)").set_defaults(f=cmd_mcp)
    a = ap.parse_args(argv)
    try:
        return a.f(a) or 0
    except (ValueError, FileNotFoundError) as e:
        log(f"error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
