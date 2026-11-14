"""zinegen command line: doctor | presets | albums | init | fetch | render | proof | check | build | spine | mcp"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from . import presets, spec

STANDARDS = ("pdf/x-1a", "pdf/x-4")


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


COUNT_SECONDS = 60   # time spent counting photos per listing; MCP clients time out at about 120 s
COUNT_WORKERS = 6
# lr-mcp gives up early, raising, when its next retry would start after the deadline; that wait is at most
# 13.5 s (backoff) or 10 s (Retry-After). An error this close to the deadline is a count that ran out of time.
COUNT_RETRY_MARGIN = 15
# lr_mcp.client messages of the failures it retries: transport errors and 429/502/503/504 responses
_RETRYABLE = re.compile(r" failed after \d+ attempts?: | returned (?:429|502|503|504)\b")


def list_albums(query: str | None = None) -> list[dict]:
    """Albums (not folders) whose name contains `query` (case-insensitive), in catalog order."""
    from lr_mcp import client as lr
    q = (query or "").lower()
    out = []
    for alb in lr.paginate(lr.catalog_path() + "albums?limit=500"):
        if alb.get("subtype") != "collection":
            continue
        if q and q not in alb.get("payload", {}).get("name", "").lower():
            continue
        out.append(alb)
    return out


def album_counts(albums: list[dict], seconds: float = COUNT_SECONDS,
                 hint: str = "narrow the listing with a query") -> tuple[dict[str, int], str]:
    """({album_id: photo count}, note) for `albums`, counted in parallel within `seconds`.

    Counts come from `lr_mcp.client.album_asset_count` (videos included; one request per 500 photos) and
    are cached in the process for up to 10 minutes while the album's `updated` is unchanged. An album not
    counted in time, or whose count failed, is left out of the dict and the note says how many and why
    (`hint` follows the time-limit part); the note is empty when every album was counted. A failed count
    never fails the listing.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor

    from lr_mcp import client as lr
    if not albums:
        return {}, ""
    lr.catalog_path()     # resolve the catalog once here, not in every worker at the same time
    deadline = time.monotonic() + seconds

    def count(alb):
        try:
            return lr.album_asset_count(alb["id"], alb.get("updated"), deadline)
        except Exception as e:  # noqa: BLE001 - one bad album or response must not lose the listing
            # cut short by the deadline, or a retryable failure lr-mcp abandoned because the next retry would
            # pass it: late, not broken. A 403 or 500 is never retried, so it stays an error even then.
            now = time.monotonic()
            if now > deadline or (now + COUNT_RETRY_MARGIN > deadline and _RETRYABLE.search(str(e))):
                return None
            return e

    with ThreadPoolExecutor(COUNT_WORKERS) as ex:
        results = list(ex.map(count, albums))
    counts = {alb["id"]: r for alb, r in zip(albums, results, strict=True) if isinstance(r, int)}
    late = sum(r is None for r in results)
    errors = [r for r in results if isinstance(r, Exception)]
    notes = []
    if late:
        notes.append(f"{late} of {len(albums)} albums not counted, time limit of {seconds:g} s reached: {hint}")
    if errors:
        e = errors[0]
        notes.append(f"{len(errors)} could not be counted: {e if isinstance(e, lr.LrError) else repr(e)}")
    return counts, "; ".join(notes)


def photo_count(counts: dict[str, int], album_id: str) -> str:
    """'<n> photos' from `album_counts`, '? photos' for an album it did not count."""
    n = counts.get(album_id, "?")
    return f"{n} photo{'' if n == 1 else 's'}"


def cmd_albums(a):
    albums = list_albums(a.query)
    hint = "use a narrower query" if a.query else "narrow the listing with a query"
    counts, note = album_counts(albums, hint=hint) if a.counts else ({}, "")
    for alb in albums:
        line = f"{alb['id']}  {alb.get('payload', {}).get('name', '')}  [{alb['payload'].get('assetSortOrder')}]"
        print(line + (f"  {photo_count(counts, alb['id'])}" if a.counts else ""))
    if note:
        log(note)


def _book(path) -> spec.Book:
    p = Path(path)
    if p.is_dir():
        p = p / "book.yaml"
    return spec.load(p)


ALBUM_ID = re.compile(r"^[0-9a-f]{32}$")


def check_album_id(album_id: str) -> str:
    """Fail fast on something that is not a Lightroom album id (a name would be retried for ~30 s)."""
    if not ALBUM_ID.match(album_id or ""):
        raise ValueError(f"{album_id!r} is not a Lightroom album id (32 lowercase hex characters); "
                         "look the id up with `zinegen albums`")
    return album_id


def init_book(album: str, dir: str | None = None, standard: str = "pdf/x-1a", icc: str | None = None,
              force: bool = False, log=None) -> dict:
    """Draft book.yaml and photos.json for an album. Writes nothing to stdout.

    Returns {"path", "photos", "sort", "icc", "warnings"}; `path` is absolute.
    Raises ValueError for a bad standard/icc/album id, FileExistsError when book.yaml exists and not `force`.
    """
    from . import color
    from .lr import compat
    if standard not in STANDARDS:
        raise ValueError(f"standard must be one of {', '.join(STANDARDS)}, not {standard!r}")
    check_album_id(album)
    info = compat.album_info(album)
    photos = compat.album_assets(album)
    name = re.sub(r"[^\w가-힣-]+", "_", info["name"] or album).strip("_")
    root = (Path(dir).expanduser() if dir else Path("books") / name).resolve()
    if icc:
        # resolved exactly as render will resolve output.icc: a relative path against the book directory
        prof = color.find_profile(icc, base=root)
        if not prof:
            raise ValueError(f"ICC profile {icc!r} not found (a file path, relative paths resolved against the book "
                             f"directory {root}, or a name searched in "
                             f"{', '.join(str(d) for d in color.ADOBE_PROFILE_DIRS)})")
        color.check_profile(prof, color.profile_label("ICC profile", icc, prof))
        if standard == "pdf/x-1a" and color.profile_components(prof) != ["c", "m", "y", "k"]:
            raise ValueError(f"pdf/x-1a needs a CMYK output profile; {prof.name} is not CMYK")
    out = root / "book.yaml"
    if out.exists() and not force:
        raise FileExistsError(f"{out} already exists")
    root.mkdir(parents=True, exist_ok=True)
    warnings = []
    if not dir and name != (info["name"] or album):
        warnings.append(f"book directory named {name!r} (the album name with characters other than letters, digits, "
                        "'-' and '_' replaced by '_'); pass dir to choose another")
    icc = icc or next((p for p in ("JapanColor2001Coated", "CoatedFOGRA39") if color.find_profile(p)), None)
    if standard == "pdf/x-1a" and not icc:
        warnings.append("no CMYK press profile found; set output.icc in book.yaml (path or profile name) before rendering")
    s = spec.skeleton(info, photos, standard, icc)
    spec.dump(s, out)
    (root / "photos.json").write_text(json.dumps(compat.photos_document(info, photos), indent=1, ensure_ascii=False))
    if log:
        log(f"wrote {out} with {len(photos)} photos (album order: {info['sort']}); photo metadata in {root / 'photos.json'}")
    return {"path": out, "photos": len(photos), "sort": info["sort"], "icc": icc, "warnings": warnings}


def cmd_init(a):
    try:
        res = init_book(a.album, a.dir, a.standard, a.icc, a.force, log=log)
    except FileExistsError as e:
        log(f"{e}; use --force to overwrite")
        return 1
    for w in res["warnings"]:
        log(f"warning: {w}")
    print(res["path"])
    return 0


def fetch_book(book: spec.Book, force: bool = False, log=None) -> dict:
    """Download the renditions book.yaml references. Writes nothing to stdout.

    Photos outside the album are looked up by id. Returns the `fetch_renditions` summary plus
    `outside_album` (found by id, not in the album) and `missing` (not found in the catalog at all).
    """
    from .lr import compat
    album = book.source.get("album_id")
    wanted = book.photo_ids()
    photos = [p for p in compat.album_assets(album) if p.id in set(wanted)] if album else []
    outside = [i for i in wanted if i not in {p.id for p in photos}]
    if outside:
        from lr_mcp import client as lr
        for i in range(0, len(outside), 100):
            ids = ",".join(outside[i:i + 100])
            for asset in lr.get_json(lr.catalog_path() + f"assets?asset_ids={ids}").get("resources", []):
                photos.append(compat._photo(asset))
    found = {p.id for p in photos}
    res = compat.fetch_renditions(photos, book.cache_dir, book.source.get("size", "fullsize"), force, log=log)
    res["outside_album"] = [i for i in outside if i in found]
    res["missing"] = [i for i in wanted if i not in found]
    spec.record_missing(book, res["missing"])
    return res


def cmd_fetch(a):
    res = fetch_book(_book(a.book), a.force, log=log)
    log(json.dumps(res, ensure_ascii=False))
    if res["missing"]:
        log(f"error: {len(res['missing'])} photo id(s) in book.yaml not found in Lightroom: {', '.join(res['missing'])}")
    return 0 if not (res["errors"] or res["missing"]) else 1


def cmd_render(a):
    from . import render
    book = _book(a.book)
    rep = render.render(book, log=log)
    for it in rep["issues"]:
        log(f"{it['level']}: {it['code']}: " + (f"page {it['page']}: " if it.get("page") else "") + it["message"])
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


def spine(pages: int, caliper_um: float, binding: str = "perfect", board_mm: float = 2.0) -> tuple[float, int]:
    """Validated spine width in mm and the page count it is computed for (`pages` rounded up to the binding's
    page multiple: a book cannot have a part sheet or signature); shared by `zinegen spine` and zg_spine."""
    if pages <= 0:
        raise ValueError(f"pages must be greater than 0, not {pages}")
    if caliper_um <= 0:
        raise ValueError(f"caliper must be greater than 0 µm, not {caliper_um}")
    if board_mm < 0:
        raise ValueError(f"board must be 0 mm or more, not {board_mm}")
    if binding not in presets.BINDINGS:
        raise ValueError(f"binding must be one of {', '.join(presets.BINDINGS)}, not {binding!r}")
    rounded = presets.round_up_pages(pages, binding)
    return presets.spine_mm(rounded, caliper_um, binding, board_mm), rounded


def spine_message(pages: int, caliper_um: float, binding: str = "perfect", board_mm: float = 2.0) -> str:
    mm, rounded = spine(pages, caliper_um, binding, board_mm)
    note = f"for {rounded} pages, {pages} rounded up to a multiple of {presets.BINDINGS[binding]['page_multiple']}" \
        if rounded != pages else f"for {pages} pages"
    return f"{mm} mm ({note}, {binding})"


def cmd_spine(a):
    print(spine_message(a.pages, a.caliper, a.binding, a.board))


def cmd_mcp(_):
    from .server import main as serve
    serve()


def version() -> str:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _v
    try:
        return _v("zinegen")
    except PackageNotFoundError:
        return "unknown"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="zinegen", description=__doc__)
    ap.add_argument("--version", action="version", version=f"zinegen {version()}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(f=cmd_doctor)
    sub.add_parser("presets").set_defaults(f=cmd_presets)
    p = sub.add_parser("albums", help="list Lightroom albums")
    p.add_argument("query", nargs="?")
    p.add_argument("--counts", action="store_true",
                   help=f"add each album's photo count: one request per album, at most {COUNT_SECONDS} s in all, "
                        "'?' for albums not counted")
    p.set_defaults(f=cmd_albums)
    p = sub.add_parser("init", help="draft book.yaml from a Lightroom album")
    p.add_argument("--album", required=True)
    p.add_argument("--dir")
    p.add_argument("--standard", default="pdf/x-1a", choices=STANDARDS)
    p.add_argument("--icc")
    p.add_argument("--force", action="store_true")
    p.set_defaults(f=cmd_init)
    for name, f, help_ in (("fetch", cmd_fetch, "download renditions into cache/"), ("render", cmd_render, "write out/interior.pdf"),
                           ("check", cmd_check, "structural PDF/X check"), ("build", cmd_build, "fetch + render + proof + check")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("book", nargs="?", default="book.yaml")
        p.add_argument("--force", action="store_true")
        p.add_argument("--dpi", type=int, default=50)
        p.set_defaults(f=f)
    p = sub.add_parser("proof", help="render spread proofs")
    p.add_argument("book", nargs="?", default="book.yaml")
    p.add_argument("--dpi", type=int, default=50)
    p.set_defaults(f=cmd_proof)
    p = sub.add_parser("spine", help="spine width from page count and paper caliper")
    p.add_argument("--pages", type=int, required=True)
    p.add_argument("--caliper", type=float, required=True, help="paper caliper in µm")
    p.add_argument("--binding", default="perfect", choices=list(presets.BINDINGS))
    p.add_argument("--board", type=float, default=2.0, help="hardcover board thickness in mm")
    p.set_defaults(f=cmd_spine)
    sub.add_parser("mcp", help="run the MCP server (stdio)").set_defaults(f=cmd_mcp)
    a = ap.parse_args(argv)
    from lr_mcp.client import LrError

    from .render import RenderError
    try:
        return a.f(a) or 0
    except (ValueError, FileNotFoundError, LrError, RenderError) as e:
        log(f"error: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
