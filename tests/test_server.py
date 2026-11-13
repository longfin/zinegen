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
    monkeypatch.setattr(color, "find_profile", lambda name: None)
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
    assert again.is_error and "already exists" in text(again)
    assert not call("zg_init_book", {"album_id": ALBUM, "standard": "pdf/x-4", "force": True}).is_error


def test_init_x1a_without_profile_warns(lightroom, tmp_path, monkeypatch):
    from zinegen import color
    monkeypatch.setattr(color, "find_profile", lambda name: None)
    res = call("zg_init_book", {"album_id": ALBUM, "dir": str(tmp_path / "b")})
    assert not res.is_error and "warning: no CMYK press profile" in text(res)


def test_init_unknown_icc_is_error(lightroom, tmp_path):
    res = call("zg_init_book", {"album_id": ALBUM, "dir": str(tmp_path / "b"), "icc": "NoSuchProfile"})
    assert res.is_error and "NoSuchProfile" in text(res) and "not found" in text(res)
    assert not (tmp_path / "b").exists()


def test_stray_print_goes_to_stderr(capfd):
    @server._tool
    def zz_noisy() -> str:
        print("stray")
        return "ok"
    try:
        res = call("zz_noisy")
    finally:
        server.mcp.remove_tool("zz_noisy")
    out, err = capfd.readouterr()
    assert text(res) == "ok" and "stray" not in out and "stray" in err


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


def test_cli_spine_rejects_non_positive(capsys):
    with pytest.raises(SystemExit):
        cli.main(["spine", "--pages", "-4", "--caliper", "100"])
    assert "greater than 0" in capsys.readouterr().err
