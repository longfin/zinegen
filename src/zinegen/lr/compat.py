"""Lightroom data in the shapes zinegen uses.

Album order and photo metadata come from `lr_mcp.catalog` (lr-mcp 0.5); this module turns them into
`Photo` values and the `photos.json` document, and words a missing album for zinegen's tools. The
rendition cache stays here: lr-mcp's downloader is an MCP tool that answers in text and keeps its own
sidecar, while `fetch` needs a per-photo result and `cache/index.json`. Everything is read-only against
the Partner API and goes through `lr_mcp.client`.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from lr_mcp import catalog
from lr_mcp import client as lr
from lr_mcp.client import LrError

__all__ = ["LrError", "Photo", "album_info", "album_assets", "fetch_renditions", "photos_document"]

PHOTOS_VERSION = 1


@dataclass
class Photo:
    id: str
    file: str | None
    captured: str | None
    order: str | None            # album-asset payload.order (custom sort key)
    title: str | None
    caption: str | None
    keywords: list[str]
    rating: int | None
    flag: str | None
    camera: str | None
    lens: str | None
    original: tuple[int, int] | None   # importSource.originalWidth/Height
    cropped: tuple[int, int] | None    # develop.croppedWidth/Height
    orientation: int | None            # develop.userOrientation (EXIF value)
    changed_at: str | None             # payload.changedAtTime, else userUpdated (moves on any change, ratings too)
    edited_at: str | None = None       # lr_mcp.catalog.asset_edited: the last develop edit

    def to_dict(self):
        return asdict(self)


def _photo(asset: dict, order: str | None = None) -> Photo:
    """An asset resource as a Photo. Every field but `changed_at` is `lr_mcp.catalog.asset_summary`'s;
    `changed_at` is not part of it and photos.json keeps the field, so it is read from the payload here."""
    s = catalog.asset_summary(asset)
    p = asset.get("payload")
    p = p if isinstance(p, dict) else {}
    changed = next((v for v in (p.get("changedAtTime"), p.get("userUpdated")) if isinstance(v, str) and v), None)
    return Photo(
        id=s["id"], file=s["file"], captured=s["captured"], order=order, title=s["title"], caption=s["caption"],
        keywords=s["keywords"], rating=s["rating"], flag=s["flag"], camera=s["camera"], lens=s["lens"],
        original=s["original"], cropped=s["cropped"], orientation=s["orientation"],
        changed_at=changed, edited_at=s["edited"],
    )


def photos_document(album: dict, photos: list[Photo]) -> dict:
    """photos.json (version 1): album info and the photos in album order."""
    return {"version": PHOTOS_VERSION, "album": {"id": album["id"], "name": album.get("name"), "sort": album.get("sort")},
            "photos": [p.to_dict() for p in photos]}


def _not_found(album_id: str) -> LrError:
    """lr-mcp's 404 message points at its own `lr_list_albums` tool; say it in zinegen's terms."""
    e = LrError(f"album {album_id} not found in the Lightroom catalog: the id may be mistyped, from "
                "another account, or the album was deleted; list the albums to find its id")
    e.status = 404
    return e


def album_info(album_id: str) -> dict:
    """`lr_mcp.catalog.album_info`: {"id", "name", "parent", "subtype", "sort"}, where `sort` is the
    album's `assetSortOrder` (captureDateAsc | captureDateDesc | customAsc). lr-mcp fetches an album
    at most once a minute."""
    try:
        return catalog.album_info(album_id)
    except LrError as e:
        if e.status == 404:
            raise _not_found(album_id) from None
        raise


def album_assets(album_id: str, images_only: bool = True) -> list[Photo]:
    """Photos of an album in the album's own order, as `lr_mcp.catalog.album_assets` sorts them: the
    custom order when the album uses one (photos without an order key last), else by capture date."""
    try:
        entries = catalog.album_assets(album_id, images_only)
    except LrError as e:
        if e.status == 404:
            raise _not_found(album_id) from None
        raise
    return [_photo(e["asset"], e["order"]) for e in entries]


def fetch_renditions(photos: list[Photo], dest: Path, size: str = "fullsize", force: bool = False,
                     workers: int = 4, log=None) -> dict:
    """Download renditions into `dest/<asset_id>.jpg` with a sidecar `dest/index.json`.

    A photo is skipped when its cached rendition has the same size and the same `edited_at`, so rating
    and flag changes do not trigger a download; a photo with no edit time at all is always downloaded.
    An index entry written by zinegen 0.2.0 has no `edited_at`; it still counts as current while its
    `changed_at` matches, and is then upgraded in place. Returns the summary {downloaded, skipped, errors}.

    Each photo gets one `lr.rendition` call, and a second one only when lr-mcp marks the failure
    `retryable`: a 2560/fullsize rendition it asked Lightroom to generate that was not ready by the end
    of its polling, or whose generate request (a POST, which lr-mcp sends once) got a 429, a 5xx or a
    dropped connection. Nothing else is tried again: lr-mcp already retried a GET on dropped connections
    and 429/502/503/504, and a 404 on a stored size (the photo is gone), a 403 or a 500 would fail the
    same way.
    """
    if size not in ("640", "1280", "2048", "2560", "fullsize"):
        raise LrError("size must be one of 640, 1280, 2048, 2560, fullsize")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    index_file = dest / "index.json"
    index = json.loads(index_file.read_text()) if index_file.exists() else {}
    jobs = []
    unique = list({p.id: p for p in photos}.values())  # one download (and one writer) per asset id
    upgraded = False
    for p in unique:
        path = dest / f"{p.id}.jpg"
        entry = index.get(p.id)
        if not force and entry and path.exists() and entry.get("size") == size:
            # an unknown edit time means download again, as lr-mcp's own cache does
            if "edited_at" in entry:
                current = p.edited_at is not None and entry["edited_at"] == p.edited_at
            else:  # 0.2.0 entry: only changed_at was recorded
                current = p.changed_at is not None and entry.get("changed_at") == p.changed_at
                if current:
                    index[p.id] = {**entry, "edited_at": p.edited_at}
                    upgraded = True
            if current:
                continue
        jobs.append((p, path))
    errors = {}
    lock = threading.Lock()

    def save_index():
        # Called under `lock` after every download, so an interrupted run keeps what it already fetched.
        _atomic_write(index_file, json.dumps(index, indent=2).encode())

    def fetch(job):
        p, path = job
        for attempt in range(2):
            try:
                data = lr.rendition(p.id, size)
            except LrError as e:
                if attempt == 0 and e.retryable:
                    time.sleep(2)
                    continue
                with lock:
                    errors[p.id] = str(e)
                return
            _atomic_write(path, data)  # never leave a truncated JPEG under the final name
            with lock:
                index[p.id] = {"size": size, "changed_at": p.changed_at, "edited_at": p.edited_at, "bytes": len(data),
                               "file": p.file, "fetched_at": int(time.time())}
                save_index()
            if log:
                log(f"fetched {p.file or p.id} ({len(data) // 1024} KB)")
            return

    if upgraded:
        save_index()
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(fetch, jobs))
    with lock:
        save_index()
    return {"downloaded": len(jobs) - len(errors), "skipped": len(unique) - len(jobs), "errors": errors}


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via a temp file unique to this writer, then rename over `path`.

    Concurrent writers of the same path (two threads, or the CLI and the MCP server fetching one book)
    each get their own temp name, so the last rename wins and none fails. The temp file is removed if
    the write or the rename fails.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
