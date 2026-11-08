"""Lightroom helpers that lr-mcp does not provide yet (see lr-mcp issues #9, #10, #11).

Reuses lr-mcp's auth, HTTP and rendition code so this module can move upstream unchanged.
Everything here is read-only against the Partner API.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

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
    changed_at: str | None             # payload.changedAtTime (edit timestamp)
    extra: dict = field(default_factory=dict)

    def to_dict(self):
        d = asdict(self)
        d.pop("extra", None)
        return d


def _latest(d, key):
    if not d:
        return None
    v = max(d.values(), key=lambda x: x.get("date", ""))
    return v.get(key)


def _photo(album_asset: dict) -> Photo:
    a = album_asset.get("asset", album_asset)
    p = a.get("payload", {})
    xmp = p.get("xmp", {})
    dc = xmp.get("dc", {})
    tiff = xmp.get("tiff", {})
    imp = p.get("importSource", {})
    dev = p.get("develop", {})
    subject = dc.get("subject")
    if isinstance(subject, dict):  # xmp bags sometimes arrive as {index: value}
        subject = list(subject.values())
    return Photo(
        id=a["id"],
        file=imp.get("fileName"),
        captured=p.get("captureDate"),
        order=album_asset.get("payload", {}).get("order") if "asset" in album_asset else None,
        title=dc.get("title") if isinstance(dc.get("title"), str) else None,
        caption=dc.get("description") if isinstance(dc.get("description"), str) else None,
        keywords=list(subject) if isinstance(subject, list) else [],
        rating=_latest(p.get("ratings"), "rating"),
        flag=_latest(p.get("reviews"), "flag"),
        camera=" ".join(filter(None, [tiff.get("Make"), tiff.get("Model")])) or None,
        lens=xmp.get("aux", {}).get("Lens"),
        original=(imp["originalWidth"], imp["originalHeight"]) if imp.get("originalWidth") else None,
        cropped=(dev["croppedWidth"], dev["croppedHeight"]) if dev.get("croppedWidth") else None,
        orientation=dev.get("userOrientation"),
        changed_at=p.get("changedAtTime") or p.get("userUpdated"),
        extra={"subtype": a.get("subtype")},
    )


def photos_document(album: dict, photos: list[Photo]) -> dict:
    """photos.json (version 1): album info and the photos in album order."""
    return {"version": PHOTOS_VERSION, "album": {"id": album["id"], "name": album.get("name"), "sort": album.get("sort")},
            "photos": [p.to_dict() for p in photos]}


def album_info(album_id: str) -> dict:
    """Album name, parent and sort mode (`assetSortOrder`: captureDateAsc | captureDateDesc | customAsc)."""
    a = lr.get_json(lr.catalog_path() + f"albums/{album_id}")
    p = a.get("payload", {})
    return {"id": a["id"], "name": p.get("name"), "sort": p.get("assetSortOrder") or "captureDateAsc",
            "parent": (p.get("parent") or {}).get("id")}


def album_assets(album_id: str, images_only: bool = True) -> list[Photo]:
    """Photos of an album in the album's own order (custom order when the album uses one)."""
    info = album_info(album_id)
    rows = list(lr.paginate(lr.catalog_path() + f"albums/{album_id}/assets?embed=asset&limit=500"))
    photos = [_photo(r) for r in rows]
    if images_only:
        photos = [p for p in photos if p.extra.get("subtype") == "image"]
    sort = info["sort"]
    if sort == "customAsc":
        photos.sort(key=lambda p: p.order or "")
    elif sort == "captureDateDesc":
        photos.sort(key=lambda p: p.captured or "", reverse=True)
    else:
        photos.sort(key=lambda p: p.captured or "")
    return photos


def fetch_renditions(photos: list[Photo], dest: Path, size: str = "fullsize", force: bool = False,
                     workers: int = 4, log=None) -> dict:
    """Download renditions into `dest/<asset_id>.jpg` with a sidecar `dest/index.json`.

    Unchanged photos (same `changed_at` and size) are skipped. Returns the index.
    """
    if size not in ("640", "1280", "2048", "2560", "fullsize"):
        raise LrError("size must be one of 640, 1280, 2048, 2560, fullsize")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    index_file = dest / "index.json"
    index = json.loads(index_file.read_text()) if index_file.exists() else {}
    jobs = []
    for p in photos:
        path = dest / f"{p.id}.jpg"
        entry = index.get(p.id)
        if (not force and entry and path.exists() and entry.get("size") == size
                and entry.get("changed_at") == p.changed_at):
            continue
        jobs.append((p, path))
    errors = {}

    def fetch(job):
        p, path = job
        for attempt in range(3):
            try:
                data = lr.rendition(p.id, size)
                path.write_bytes(data)
                index[p.id] = {"size": size, "changed_at": p.changed_at, "bytes": len(data),
                               "file": p.file, "fetched_at": int(time.time())}
                if log:
                    log(f"fetched {p.file or p.id} ({len(data) // 1024} KB)")
                return
            except LrError as e:
                errors[p.id] = str(e)
                time.sleep(2 * (attempt + 1))

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(fetch, jobs))
    index_file.write_text(json.dumps(index, indent=2))
    return {"downloaded": len(jobs) - len(errors), "skipped": len(photos) - len(jobs), "errors": errors}
