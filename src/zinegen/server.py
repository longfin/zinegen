"""MCP server exposing the zinegen pipeline to any agent (Claude Code, Codex, ...).

Two rules every tool follows:

- Nothing but JSON-RPC may reach stdout (it is the stdio transport), so tools call functions that return
  values and log to stderr, never the `cmd_*` CLI wrappers, and stray prints are redirected to stderr.
- Expected failures (bad book.yaml, missing files, wrong order of steps, bad arguments, Lightroom errors)
  are raised as `ToolError` so their message reaches the agent. Any other exception is a bug; the SDK
  reports it as a bare "Error executing tool <name>" and logs the traceback on stderr.
"""
from __future__ import annotations

import contextlib
import functools
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal

import yaml
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import cli, presets, spec

MAX_PROOF_IMAGES = 12

# Argument validation errors reach the agent as pydantic's text; its "For further information visit
# https://errors.pydantic.dev/..." line is noise there. pydantic-core reads this when it first formats an error.
os.environ.setdefault("PYDANTIC_ERRORS_INCLUDE_URL", "0")

_STDIO_MAIN = False   # set by main(); in-process clients (tests) leave sys.stdout alone
_wire_stdout = None   # the replaced sys.stdout, kept alive: in mcp's fallback the transport writes to its buffer


@contextlib.asynccontextmanager
async def _lifespan(_server):
    """Runs after the stdio transport has taken its own reference to the protocol stream (see main)."""
    global _wire_stdout
    if _STDIO_MAIN and sys.stdout is not sys.stderr:
        # Dropping the last reference would close the text wrapper and with it the buffer the
        # transport may be serving in place (mcp issue #1933), so keep it.
        _wire_stdout, sys.stdout = sys.stdout, sys.stderr
    yield {}


mcp = MCPServer(
    "zinegen",
    lifespan=_lifespan,
    version=cli.version(),
    instructions=(
        "Typeset a photobook from a Lightroom album into a print-ready PDF/X. Workflow: zg_list_albums -> "
        "zg_init_book (draft book.yaml) -> edit book.yaml (sequence, templates, captions) -> zg_fetch -> "
        "zg_render -> zg_proof (look at the spreads) -> iterate -> zg_check. The book.yaml is the single "
        "source of truth; keep edits there. Use zg_spec_help for the template reference."
    ),
)

Standard = Literal["pdf/x-1a", "pdf/x-4"]
Binding = Literal["saddle", "perfect", "hardcover", "layflat"]   # presets.BINDINGS (a test keeps them equal)
BookArg = Annotated[str, Field(description="Book directory holding book.yaml, or the book.yaml path. "
                                           "Absolute, or relative to the server's working directory.")]
AlbumId = Annotated[str, Field(description="Lightroom album id (32 hex characters) from zg_list_albums")]

# Shared pipeline code names CLI commands exactly as "`zinegen <cmd>`" (backticks, no arguments). Only that
# phrase is replaced by the tool name, so paths or longer commands containing the same words stay intact.
_CLI_TO_TOOL = {"albums": "zg_list_albums", "init": "zg_init_book", "fetch": "zg_fetch", "render": "zg_render",
                "proof": "zg_proof", "check": "zg_check", "spine": "zg_spine"}
_CLI_RE = re.compile(r"`zinegen (" + "|".join(_CLI_TO_TOOL) + r")`")
# lr_mcp.client messages carry API paths with the catalog id, either relative after a space
# (`GET /v2/catalogs/<id>/albums/x returned 404`) or absolute (`GET https://lr.adobe.io/v2/catalogs/<id>/...
# failed`). Only those shapes match: the Lightroom host or a path starting a word, then a hex id that ends
# the path segment. `.../<id>/rest` becomes `rest`; a bare `.../<id>` becomes `catalog`.
_LR_PATH_RE = re.compile(r"(?:https://lr\.adobe\.io|(?<![^\s'\"(\[]))/v2/catalogs/[0-9a-f]+(?![\w.-])(/?)")


def _for_agent(msg: str) -> str:
    """Name zg_* tools instead of CLI commands and drop the Lightroom API path (it carries the catalog id)."""
    msg = _CLI_RE.sub(lambda m: f"`{_CLI_TO_TOOL[m.group(1)]}`", msg)
    return _LR_PATH_RE.sub(lambda m: "" if m.group(1) else "catalog", msg)


def _tool(fn):
    """Register `fn` as a tool whose expected failures reach the agent with their message."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError as e:                       # includes lr_mcp's LrError
            raise ToolError(_for_agent(str(e))) from e
        except yaml.YAMLError as e:
            raise ToolError(f"book.yaml is not valid YAML: {e}") from e
        except subprocess.CalledProcessError as e:
            tail = (e.stderr or b"")
            tail = (tail.decode(errors="replace") if isinstance(tail, bytes) else tail).strip()[-800:]
            raise ToolError(f"{Path(str(e.cmd[0])).name} failed (exit {e.returncode})" + (f": {tail}" if tail else "")) from e
        except (ValueError, OSError, RuntimeError) as e:  # SpecError is a ValueError; FileNotFound etc. are OSError
            raise ToolError(_for_agent(str(e))) from e
        finally:
            # Under main() sys.stdout is already stderr; the flush only matters if something swapped it
            # back. It runs while fd 1 still points at stderr, so nothing stays buffered for shutdown.
            with contextlib.suppress(Exception):
                sys.stdout.flush()
    return mcp.tool()(wrapper)


def _book(path: str) -> spec.Book:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"{p} does not exist; pass a book directory (holding book.yaml) or a book.yaml path")
    if p.is_dir():
        if not (p / "book.yaml").exists():
            raise FileNotFoundError(f"no book.yaml in {p}; draft one with zg_init_book")
        p = p / "book.yaml"
    return spec.load(p)


@_tool
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
              "page_numbers (default false; folios on every page except title, blank, full-bleed and spreads), "
              "marks (default true; crop marks outside the bleed), output {standard pdf/x-1a|pdf/x-4, icc (name, or path relative to the book directory), intent, jpeg_quality, max_dpi (x-1a downsampling target, default 350, null = keep all pixels)}, source {album_id,size}, pages[]"]
    return "\n".join(lines)


@_tool
def zg_list_albums(
    query: Annotated[str | None, Field(description="Case-insensitive substring of the album name")] = None,
    include_counts: Annotated[bool, Field(description="Append each album's photo count (videos included). Costs "
                                                      "one request per album, so pass a query when you can")] = False,
) -> str:
    """Lightroom albums, one per line: id | name | sort mode.

    With include_counts each line ends with '| <n> photos'. Counting stops after 60 s: albums not counted
    by then, or whose count failed, show '? photos' and a last line says how many and why. Finished counts
    are cached for up to 10 minutes, so calling again continues where the last call stopped."""
    albums = cli.list_albums(query)
    if not albums:
        return "no albums"
    rows = [f"{alb['id']} | {alb.get('payload', {}).get('name', '')} | {alb['payload'].get('assetSortOrder')}"
            for alb in albums]
    if not include_counts:
        return "\n".join(rows)
    counts, note = cli.album_counts(albums, hint="call again to count the rest (finished counts are cached), "
                                                 "or narrow with query")
    rows = [f"{row} | {cli.photo_count(counts, alb['id'])}" for row, alb in zip(rows, albums, strict=True)]
    if note:
        rows.append(_for_agent(note))
    return "\n".join(rows)


@_tool
def zg_album_photos(album_id: AlbumId) -> str:
    """Photos of an album in the album's own order, with title/caption and cropped size. One line per photo."""
    from .lr import compat
    cli.check_album_id(album_id)
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


@_tool
def zg_init_book(
    album_id: AlbumId,
    dir: Annotated[str | None, Field(description="Book directory to create; default books/<album name> "
                                                 "under the server's working directory")] = None,
    standard: Annotated[Standard, Field(description="PDF/X flavour: pdf/x-1a (CMYK, needs a CMYK icc) "
                                                    "or pdf/x-4 (ICC-tagged RGB)")] = "pdf/x-1a",
    icc: Annotated[str | None, Field(description="Press ICC profile: an absolute path, a path relative to "
                                                  "the book directory, or a profile name such as "
                                                  "JapanColor2001Coated; default: the first one found")] = None,
    force: Annotated[bool, Field(description="Overwrite an existing book.yaml")] = False,
) -> str:
    """Draft book.yaml (and photos.json) for an album. Returns the absolute path of the book.yaml written."""
    try:
        res = cli.init_book(album_id, dir, standard, icc, force, log=cli.log)
    except FileExistsError as e:
        raise ToolError(f"{e}; pass force=true to overwrite") from e
    lines = [f"drafted {res['path']} ({res['photos']} photos, album order {res['sort']}, icc {res['icc'] or 'none'})"]
    lines += [f"warning: {w}" for w in res["warnings"]]
    lines.append("read it, then edit sequence/templates/captions and call zg_fetch + zg_render")
    return "\n".join(lines)


@_tool
def zg_fetch(
    book: BookArg,
    force: Annotated[bool, Field(description="Download again even if the cached rendition is current")] = False,
) -> str:
    """Download (and cache) the renditions referenced by book.yaml.

    Returns JSON: downloaded, skipped, errors {id: message}, outside_album (ids found in the catalog but
    not in the album), missing (ids not found at all; fix book.yaml before zg_render).
    """
    res = cli.fetch_book(_book(book), force, log=cli.log)
    res["errors"] = {pid: _for_agent(msg) for pid, msg in res["errors"].items()}
    if res["missing"]:
        res["warning"] = (f"{len(res['missing'])} photo id(s) in book.yaml were not found in Lightroom; "
                          "replace or remove them before zg_render")
    return json.dumps(res, ensure_ascii=False)


@_tool
def zg_render(book: BookArg) -> str:
    """Render book.yaml to out/interior.pdf (PDF/X) and out/report.json. Returns the report summary."""
    from . import render
    b = _book(book)
    rep = render.render(b, log=cli.log)
    return json.dumps({"pdf": rep["pdf"], "pages": rep["pages"], "issues": rep["issues"]}, ensure_ascii=False)


def _spread_pages(n: int, page_count: int) -> str:
    """Pages shown on spread `n` (1-based): page 1 alone on the right, then (2,3), (4,5) ..."""
    pages = [1] if n == 1 else [p for p in (2 * n - 2, 2 * n - 1) if p <= page_count]
    if len(pages) == 2:
        return f"pages {pages[0]}-{pages[1]}"
    return f"page {pages[0]}" if pages else "pages ? (out/interior.pdf does not match book.yaml; zg_render again)"


def _pdf_spreads(b: spec.Book) -> int | None:
    """Spread count of out/interior.pdf (page 1 alone, then pairs), or None when it cannot be read; proof()
    reports a missing or broken PDF itself."""
    import pikepdf
    try:
        with pikepdf.open(b.out_dir / "interior.pdf") as pdf:
            pages = len(pdf.pages)
    except (OSError, pikepdf.PdfError):
        return None
    return 1 + pages // 2 if pages else 0


@_tool
def zg_proof(
    book: BookArg,
    spreads: Annotated[list[int] | None, Field(description="1-based spread numbers to return "
                                                           "(spread 1 is page 1; spread n is pages 2n-2, 2n-1). "
                                                           f"Default: all, at most {MAX_PROOF_IMAGES} per call")] = None,
    dpi: Annotated[int, Field(ge=10, le=300, description="Proof resolution")] = 40,
) -> list:
    """Render spread proofs of out/interior.pdf and return them as images, each labelled with its pages.

    At most 12 spreads per call; the first text block says how many spreads the book has,
    which ones were left out and how to request them.
    """
    from . import proof
    b = _book(book)
    if spreads:
        # checked before Ghostscript runs, so a call that can return nothing leaves proof/ as it was
        n = _pdf_spreads(b)
        if n is not None and not any(0 < s <= n for s in spreads):
            raise ToolError(f"no such spread: {', '.join(map(str, spreads))} (out/interior.pdf has spreads 1-{n}; "
                            "spread 1 is page 1, spread n is pages 2n-2 and 2n-1)")
    files = proof.proof(b, dpi, log=cli.log)
    total = len(files)
    if not total:
        raise ToolError("Ghostscript found no pages in out/interior.pdf (an interrupted render?); run zg_render again")
    wanted = list(dict.fromkeys(spreads)) if spreads else list(range(1, total + 1))
    bad = [n for n in wanted if not 0 < n <= total]
    wanted = [n for n in wanted if 0 < n <= total]
    if not wanted:
        raise ToolError(f"no such spread: {', '.join(map(str, bad))} (the book has spreads 1-{total})")
    shown, rest = wanted[:MAX_PROOF_IMAGES], wanted[MAX_PROOF_IMAGES:]
    head = [f"{len(shown)} of {total} spreads" + (f" ({', '.join(map(str, shown))})" if spreads else "")
            + f"; proofs in {b.proof_dir}"]
    if rest:
        head.append(f"not shown: spreads {', '.join(map(str, rest))}; call zg_proof again with spreads={rest[:MAX_PROOF_IMAGES]}")
    if bad:
        head.append(f"out of range (the book has spreads 1-{total}): {', '.join(map(str, bad))}")
    out: list = ["\n".join(head)]
    for n in shown:
        out.append(f"spread {n}: {_spread_pages(n, b.page_count)}")
        out.append(Image(data=files[n - 1].read_bytes(), format="png"))
    return out


@_tool
def zg_check(book: BookArg) -> str:
    """Structural PDF/X check of out/interior.pdf (boxes, output intent, colour spaces, fonts, resolution)."""
    import pikepdf

    from . import check
    b = _book(book)
    pdf = b.out_dir / "interior.pdf"
    if not pdf.exists():
        raise FileNotFoundError(f"no {pdf}; run zg_render first")
    try:
        res = check.check(b)
    except pikepdf.PdfError as e:
        raise ToolError(f"{pdf} cannot be read ({e}); it is probably incomplete, run zg_render again") from e
    return json.dumps(res, indent=1, ensure_ascii=False)


@_tool
def zg_spine(
    pages: Annotated[int, Field(gt=0, description="Page count of the interior")],
    caliper_um: Annotated[float, Field(gt=0, description="Paper caliper (thickness per sheet) in µm")],
    binding: Binding = "perfect",
    board_mm: Annotated[float, Field(ge=0, description="Hardcover board thickness in mm")] = 2.0,
) -> str:
    """Spine width in mm from page count and paper caliper (µm). Hardcover adds boards."""
    return cli.spine_message(pages, caliper_um, binding, board_mm)


def _reject_unknown_arguments():
    """An argument the tool does not have (`spread` for `spreads`, `standard` on zg_render) is an error, not
    silently dropped: forbid extra fields in each tool's argument model and say so in its input schema.

    This reaches into private MCP SDK attributes. If an SDK change breaks that, the server still starts and
    unknown arguments are ignored again (test_tool_schemas_forbid_extra_arguments catches it on CI)."""
    try:
        for tool in mcp._tool_manager.list_tools():
            model = tool.fn_metadata.arg_model
            model.model_config["extra"] = "forbid"
            model.model_rebuild(force=True)
            tool.parameters["additionalProperties"] = False
    except Exception as e:  # noqa: BLE001 - never keep the server from starting over this
        print(f"zinegen: cannot make MCP tools reject unknown arguments ({type(e).__name__}: {e}); "
              "they will be ignored", file=sys.stderr)


_reject_unknown_arguments()


def main():
    # mcp.server.stdio.stdio_server reads `sys.stdout` once, when it opens the transport: normally it then
    # writes JSON-RPC to a private duplicate of fd 1 and points fd 1 at stderr; when it cannot (its fallback),
    # it writes to `sys.stdout.buffer` in place. Either way it keeps its own reference and never looks at
    # `sys.stdout` again, so once the server is running (_lifespan) `sys.stdout` is pointed at stderr for
    # the rest of the process and Python prints can never reach the wire in either mode.
    global _STDIO_MAIN
    _STDIO_MAIN = True
    sys.stdout.flush()   # nothing printed before the transport opens may be flushed onto the wire later
    mcp.run()


if __name__ == "__main__":
    main()
