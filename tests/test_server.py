"""MCP server surface, driven in-process through the SDK client. Lightroom is mocked; no network."""
from __future__ import annotations

import json
import tomllib
import typing
from pathlib import Path

import anyio
import pytest
import yaml
from mcp import Client
from PIL import Image

from zinegen import cli, presets, server, spec
from zinegen.lr import compat

ROOT = Path(__file__).resolve().parents[1]
A = "0123456789abcdef0123456789abcdef"
B = "fedcba9876543210fedcba9876543210"
ALBUM = "dbe1c845a63940b685ae72357c88f5ae"


def call(name: str, args: dict | None = None):
    async def run():
        async with Client(server.mcp) as c:
            return await c.call_tool(name, args or {})
    return anyio.run(run)


def text(res) -> str:
    return "\n".join(c.text for c in res.content if c.type == "text")


def photo(pid: str, w=3000, h=2000) -> compat.Photo:
    return compat.Photo(id=pid, file=f"{pid[:4]}.jpg", captured="2024-10-01T10:00:00", order=None, title=None,
                        caption=None, keywords=[], rating=None, flag=None, camera=None, lens=None,
                        original=(w, h), cropped=None, orientation=None, changed_at="t", extra={"subtype": "image"})


def write_book(d: Path, pages=None, **extra) -> Path:
    doc = {"version": 1, "title": "t", "trim": {"preset": "A5"}, "binding": "perfect",
           "output": {"standard": "pdf/x-4"}, "source": {"album_id": ALBUM},
           "pages": pages or [{"template": "margin", "photo": A}, {"template": "margin", "photo": B}]}
    doc.update(extra)
    d.mkdir(parents=True, exist_ok=True)
    (d / "book.yaml").write_text(yaml.safe_dump(doc))
    return d


@pytest.fixture
def lightroom(monkeypatch):
    """Album ALBUM holds photo A only; nothing else may reach the network."""
    def no_network(*a, **k):
        raise AssertionError("network access in a test")
    from lr_mcp import client as lr
    monkeypatch.setattr(lr, "request", no_network)
    monkeypatch.setattr(lr, "catalog_path", lambda: "/v2/catalogs/c0ffee/")
    monkeypatch.setattr(compat, "album_info", lambda album_id: {"id": album_id, "name": "Autumn walk",
                                                                "sort": "captureDateAsc", "parent": None})
    monkeypatch.setattr(compat, "album_assets", lambda album_id, images_only=True: [photo(A)])
    return lr


# ---------------------------------------------------------------- server info and schemas

def test_server_version_and_schemas():
    async def run():
        async with Client(server.mcp) as c:
            return c.server_info, {t.name: t.input_schema for t in (await c.list_tools()).tools}
    info, schemas = anyio.run(run)
    assert info.version == tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert schemas["zg_init_book"]["properties"]["standard"]["enum"] == list(cli.STANDARDS)
    assert schemas["zg_spine"]["properties"]["binding"]["enum"] == list(presets.BINDINGS)
    assert schemas["zg_proof"]["properties"]["book"]["description"]
    assert set(typing.get_args(server.Standard)) == set(spec.SCHEMA["properties"]["output"]["properties"]["standard"]["enum"])


# ---------------------------------------------------------------- #1 errors carry their message

def test_invalid_spec_message_reaches_agent(tmp_path):
    write_book(tmp_path, pages=[{"template": "nope"}], bogus_key=1)
    res = call("zg_render", {"book": str(tmp_path)})
    assert res.is_error
    assert "bogus_key" in text(res) and "pages[0].template" in text(res)


@pytest.mark.parametrize("tool", ["zg_fetch", "zg_render", "zg_proof", "zg_check"])
def test_missing_book(tmp_path, tool):
    res = call(tool, {"book": str(tmp_path / "nowhere")})
    assert res.is_error and "does not exist" in text(res)
    res = call(tool, {"book": str(tmp_path)})
    assert res.is_error and "no book.yaml" in text(res) and "zg_init_book" in text(res)


def test_yaml_syntax_error(tmp_path):
    (tmp_path / "book.yaml").write_text("version: 1\npages: [\n")
    res = call("zg_check", {"book": str(tmp_path)})
    assert res.is_error and "not valid YAML" in text(res)


def test_proof_before_render_names_tool(tmp_path):
    write_book(tmp_path)
    res = call("zg_proof", {"book": str(tmp_path)})
    assert res.is_error and "`zg_render`" in text(res) and "zinegen" not in text(res)


def test_render_before_fetch_names_tool(tmp_path):
    from zinegen.render import _setup_dyld
    _setup_dyld()
    pytest.importorskip("weasyprint")
    write_book(tmp_path)
    res = call("zg_render", {"book": str(tmp_path)})
    assert res.is_error and "missing cached photo" in text(res) and "`zg_fetch`" in text(res)


@pytest.mark.parametrize("tool,args", [
    ("zg_proof", {"book": "x", "dpi": 0}),
    ("zg_spine", {"pages": -4, "caliper_um": 0}),
    ("zg_spine", {"pages": 40, "caliper_um": 100, "binding": "spiral"}),
    ("zg_init_book", {"album_id": ALBUM, "standard": "pdf/x-9"}),
])
def test_bad_arguments_rejected(tool, args):
    res = call(tool, args)
    assert res.is_error and "validation error" in text(res)


def test_spine():
    res = call("zg_spine", {"pages": 100, "caliper_um": 110})
    assert not res.is_error and text(res).startswith("5.5 mm")


def test_album_name_fails_fast(lightroom, monkeypatch):
    monkeypatch.setattr(compat, "album_info", lambda album_id: pytest.fail("Lightroom called"))
    res = call("zg_album_photos", {"album_id": "Autumn walk"})
    assert res.is_error and "not a Lightroom album id" in text(res) and "zg_list_albums" in text(res)


def test_lightroom_error_hides_api_path(lightroom, monkeypatch):
    def boom(album_id):
        raise compat.LrError(f"GET /v2/catalogs/c0ffee/albums/{album_id} returned 404: not found")
    monkeypatch.setattr(compat, "album_info", boom)
    res = call("zg_album_photos", {"album_id": ALBUM})
    assert res.is_error and "returned 404" in text(res)
    assert "catalogs" not in text(res) and "c0ffee" not in text(res)


# ---------------------------------------------------------------- #2 / #3 init

def test_init_writes_nothing_to_stdout(lightroom, tmp_path, capfd, monkeypatch):
    from zinegen import color
    monkeypatch.setattr(color, "find_profile", lambda name, base=None: None)
    monkeypatch.chdir(tmp_path)
    res = call("zg_init_book", {"album_id": ALBUM, "standard": "pdf/x-4"})
    out = capfd.readouterr().out
    assert out == ""
    assert not res.is_error
    path = tmp_path / "books" / "Autumn_walk" / "book.yaml"
    assert f"drafted {path} " in text(res)
    assert spec.load(path).photo_ids() == [A]
    assert json.loads((path.parent / "photos.json").read_text())["photos"][0]["id"] == A

    again = call("zg_init_book", {"album_id": ALBUM, "standard": "pdf/x-4"})
    assert again.is_error and "already exists; pass force=true to overwrite" in text(again)
    assert not call("zg_init_book", {"album_id": ALBUM, "standard": "pdf/x-4", "force": True}).is_error


def test_init_x1a_without_profile_warns(lightroom, tmp_path, monkeypatch):
    from zinegen import color
    monkeypatch.setattr(color, "find_profile", lambda name, base=None: None)
    res = call("zg_init_book", {"album_id": ALBUM, "dir": str(tmp_path / "b")})
    assert not res.is_error and "warning: no CMYK press profile" in text(res)


def test_init_relative_icc_resolves_against_book_dir(lightroom, tmp_path, monkeypatch):
    """A relative icc means the same file at init and at render: relative to the book directory, not to the
    server's working directory."""
    import shutil as sh
    from importlib.resources import files as res
    srgb = str(res("zinegen") / "templates" / "sRGB2014.icc")
    cwd = tmp_path / "server-cwd"
    (cwd / "rel").mkdir(parents=True)
    sh.copy(srgb, cwd / "rel" / "my.icc")
    monkeypatch.chdir(cwd)
    book = tmp_path / "b"
    res_ = call("zg_init_book", {"album_id": ALBUM, "dir": str(book), "standard": "pdf/x-4", "icc": "rel/my.icc"})
    assert res_.is_error and "book directory" in text(res_)    # only exists relative to the server cwd
    (book / "rel").mkdir(parents=True)
    sh.copy(srgb, book / "rel" / "my.icc")
    res_ = call("zg_init_book", {"album_id": ALBUM, "dir": str(book), "standard": "pdf/x-4", "icc": "rel/my.icc"})
    assert not res_.is_error
    b = spec.load(book / "book.yaml")
    assert b.output["icc"] == "rel/my.icc"
    from zinegen import render
    monkeypatch.chdir(tmp_path)
    assert render.resolve_icc(b) == (book / "rel" / "my.icc").resolve()


def test_init_unknown_icc_is_error(lightroom, tmp_path):
    res = call("zg_init_book", {"album_id": ALBUM, "dir": str(tmp_path / "b"), "icc": "NoSuchProfile"})
    assert res.is_error and "NoSuchProfile" in text(res) and "not found" in text(res)
    assert not (tmp_path / "b").exists()


NOISY_SERVER = """
import io, os, sys, threading, time
if sys.argv[1] == "fallback":
    # sys.stdout no longer backed by fd 1: mcp cannot divert fd 1 and serves the wire through
    # sys.stdout.buffer in place (its documented fallback)
    sys.stdout = io.TextIOWrapper(os.fdopen(os.dup(1), "wb"), encoding="utf-8")
from zinegen import server
started = threading.Barrier(2)

@server._tool
def zz_noisy(n: int) -> str:
    print(f"stray-{n}-a", end="")      # unterminated: stays in the buffer until _tool flushes
    started.wait(timeout=5)            # two calls overlap
    time.sleep(0.1 * n)
    print(f" stray-{n}-b", end="")
    return "ok"

server.main()
"""


@pytest.mark.parametrize("mode", ["divert", "fallback"])
def test_stray_print_never_reaches_protocol_stdout(tmp_path, mode):
    """Over real stdio, prints inside overlapping tool calls go to stderr, also after the server exits,
    whether or not mcp could divert fd 1."""
    import subprocess
    import sys
    msgs = [{"jsonrpc": "2.0", "id": 0, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"}]
    msgs += [{"jsonrpc": "2.0", "id": n, "method": "tools/call", "params": {"name": "zz_noisy", "arguments": {"n": n}}}
             for n in (1, 2)]
    p = subprocess.Popen([sys.executable, "-c", NOISY_SERVER, mode], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, cwd=tmp_path)
    p.stdin.write(b"".join(json.dumps(m).encode() + b"\n" for m in msgs))
    p.stdin.flush()
    replies = {}
    while len(replies) < 3:
        line = p.stdout.readline()
        assert line, p.stderr.read().decode()[-2000:]
        msg = json.loads(line)                       # every stdout line must be JSON-RPC
        replies[msg["id"]] = msg
    p.stdin.close()
    p.wait(timeout=20)
    rest, err = p.stdout.read(), p.stderr.read().decode()
    assert rest == b""
    assert [replies[n]["result"]["content"][0]["text"] for n in (1, 2)] == ["ok", "ok"]
    assert replies[0]["result"]["serverInfo"]["version"] == cli.version()
    for s in ("stray-1-a", "stray-1-b", "stray-2-a", "stray-2-b"):
        assert s in err


# ---------------------------------------------------------------- #6 fetch reports missing ids

def test_fetch_reports_missing(lightroom, tmp_path, monkeypatch):
    write_book(tmp_path)
    monkeypatch.setattr(lightroom, "get_json", lambda path: {"resources": []})
    fetched = {}

    def fake_fetch(photos, dest, size="fullsize", force=False, workers=4, log=None):
        fetched["ids"] = [p.id for p in photos]
        return {"downloaded": len(photos), "skipped": 0, "errors": {}}
    monkeypatch.setattr(compat, "fetch_renditions", fake_fetch)
    res = call("zg_fetch", {"book": str(tmp_path)})
    assert not res.is_error
    data = json.loads(text(res))
    assert fetched["ids"] == [A]
    assert data["missing"] == [B] and data["outside_album"] == []
    assert "zg_render" in data["warning"]


def test_fetch_errors_hide_catalog_id(lightroom, tmp_path, monkeypatch):
    write_book(tmp_path, pages=[{"template": "margin", "photo": A}])
    monkeypatch.setattr(compat, "fetch_renditions", lambda photos, dest, size="fullsize", force=False, workers=4, log=None: {
        "downloaded": 0, "skipped": 0, "errors": {A: f"GET https://lr.adobe.io/v2/catalogs/c0ffee/assets/{A}/renditions/"
                                                    "fullsize failed after 3 attempts: TimeoutError()"}})
    res = call("zg_fetch", {"book": str(tmp_path)})
    assert "c0ffee" not in text(res) and "catalogs" not in text(res)
    assert json.loads(text(res))["errors"][A].startswith(f"GET assets/{A}/renditions/fullsize failed")


# ---------------------------------------------------------------- #4 proof truncation

@pytest.fixture
def fifteen_spreads(tmp_path, monkeypatch):
    from zinegen import proof
    write_book(tmp_path, pages=[{"template": "blank"}] * 28)
    files = []
    for n in range(1, 16):
        p = tmp_path / f"spread-{n:02d}.png"
        Image.new("RGB", (4, 3)).save(p)
        files.append(p)
    monkeypatch.setattr(proof, "proof", lambda book, dpi=50, log=None: files)
    return tmp_path


def test_proof_reports_truncation(fifteen_spreads):
    res = call("zg_proof", {"book": str(fifteen_spreads)})
    head = res.content[0].text
    assert head.startswith("12 of 15 spreads")
    assert "not shown: spreads 13, 14, 15" in head and "spreads=[13, 14, 15]" in head
    assert sum(c.type == "image" for c in res.content) == 12
    labels = [c.text for c in res.content[1:] if c.type == "text"]
    assert labels[0] == "spread 1: page 1" and labels[1] == "spread 2: pages 2-3"


def test_proof_out_of_range(fifteen_spreads):
    res = call("zg_proof", {"book": str(fifteen_spreads), "spreads": [15, 99, 0, 15]})
    head = res.content[0].text
    assert head.startswith("1 of 15 spreads (15)")
    assert "out of range (the book has spreads 1-15): 99, 0" in head
    assert [c.text for c in res.content[1:] if c.type == "text"] == ["spread 15: page 28"]


# ---------------------------------------------------------------- CLI

def test_cli_version(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"zinegen {cli.version()}"


@pytest.mark.parametrize("args,msg", [
    (["--pages", "-4", "--caliper", "100"], "pages must be greater than 0"),
    (["--pages", "40", "--caliper", "0"], "caliper must be greater than 0"),
    (["--pages", "40", "--caliper", "100", "--board", "-3", "--binding", "hardcover"], "board must be 0 mm or more"),
])
def test_cli_spine_rejects_bad_input(capsys, args, msg):
    assert cli.main(["spine", *args]) == 2
    out, err = capsys.readouterr()
    assert out == "" and msg in err


def test_mcp_spine_rejects_negative_board():
    res = call("zg_spine", {"pages": 40, "caliper_um": 100, "binding": "hardcover", "board_mm": -3})
    assert res.is_error


def test_cli_init_exists(lightroom, tmp_path, capsys, monkeypatch):
    from zinegen import color
    monkeypatch.setattr(color, "find_profile", lambda name, base=None: None)
    d = str(tmp_path / "b")
    assert cli.main(["init", "--album", ALBUM, "--dir", d, "--standard", "pdf/x-4"]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "b" / "book.yaml")
    assert cli.main(["init", "--album", ALBUM, "--dir", d, "--standard", "pdf/x-4"]) == 1
    assert "already exists; use --force to overwrite" in capsys.readouterr().err


# ---------------------------------------------------------------- message rewriting

@pytest.mark.parametrize("msg,want", [
    ("missing cached photo x; run `zinegen fetch` first", "missing cached photo x; run `zg_fetch` first"),
    ("/tmp/zinegen render/x does not exist", "/tmp/zinegen render/x does not exist"),
    ("run `zinegen fetch --force` first", "run `zinegen fetch --force` first"),
    ("GET /v2/catalogs/c0ffee/albums/x returned 404", "GET albums/x returned 404"),
    ("GET https://lr.adobe.io/v2/catalogs/c0ffee/assets/a/renditions/fullsize failed",
     "GET assets/a/renditions/fullsize failed"),
    ("GET https://lr.adobe.io/v2/catalogs/c0ffee returned 500", "GET catalog returned 500"),
    ("catalog /v2/catalogs/c0ffee", "catalog catalog"),
    # only the Lightroom shapes: other hosts, non-hex ids and mid-path matches are left alone
    ("/Users/me/photos/v2/catalogs/notes.txt not found", "/Users/me/photos/v2/catalogs/notes.txt not found"),
    ("docs at http://example.com/v2/catalogs/abc/x", "docs at http://example.com/v2/catalogs/abc/x"),
    ("url: https://lr.adobe.io/v2/catalogs/abc123, retry", "url: catalog, retry"),
    ("see /v2/catalogs/abcdef.txt", "see /v2/catalogs/abcdef.txt"),
])
def test_for_agent(msg, want):
    assert server._for_agent(msg) == want


def test_missing_book_path_with_cli_words(tmp_path):
    d = tmp_path / "zinegen render" / "x"
    res = call("zg_check", {"book": str(d)})
    assert res.is_error and f"{d} does not exist" in text(res)


# ---------------------------------------------------------------- unreadable out/interior.pdf

@pytest.fixture
def broken_pdf(tmp_path):
    write_book(tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "interior.pdf").write_bytes(b"%PDF-1.4\ngarbage")
    return tmp_path


def test_check_unreadable_pdf(broken_pdf):
    res = call("zg_check", {"book": str(broken_pdf)})
    assert res.is_error and "cannot be read" in text(res) and "zg_render again" in text(res)


def test_check_before_render(tmp_path):
    write_book(tmp_path)
    res = call("zg_check", {"book": str(tmp_path)})
    assert res.is_error and "zg_render first" in text(res)


def test_proof_without_pages_is_error(broken_pdf, monkeypatch):
    from zinegen import proof
    monkeypatch.setattr(proof, "render_pages", lambda pdf, dest, dpi=50: [])
    res = call("zg_proof", {"book": str(broken_pdf)})
    assert res.is_error and "no pages" in text(res) and "zg_render again" in text(res)


@pytest.mark.skipif(not __import__("shutil").which("gs"), reason="Ghostscript not installed")
def test_proof_unreadable_pdf_with_ghostscript(broken_pdf):
    res = call("zg_proof", {"book": str(broken_pdf)})
    assert res.is_error
