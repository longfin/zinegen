"""lr/compat.py helpers, without the network (lr_mcp.client is stubbed where needed)."""
import json
import threading

import pytest

from zinegen.lr import compat


def _asset(subject):
    dc = {} if subject is None else {"subject": subject}
    return {"asset": {"id": "a" * 32, "subtype": "image", "payload": {"xmp": {"dc": dc}}},
            "payload": {"order": "V--1"}}


@pytest.mark.parametrize("subject, expected", [
    ({"sea": True, "winter": True}, ["sea", "winter"]),        # shape the Partner API actually sends
    ({"sea": True, "dropped": False}, ["sea"]),
    ({"1": "winter", "0": "sea"}, ["sea", "winter"]),           # {index: value} bag
    ({0: "sea", 1: "winter"}, ["sea", "winter"]),
    (["sea", "winter"], ["sea", "winter"]),
    ("sea", ["sea"]),
    ({}, []),
    (None, []),
    # shapes never observed: must not crash and must not drop a truthy string key
    ({"²": "a"}, ["²"]),                                          # isdigit() but not int()-able
    ({"sea": True, "0": "x"}, ["x", "sea"]),                      # mixed bag
    ({"sea": 1}, ["sea"]),                                        # truthy non-bool value
    ({"2026": True}, ["2026"]),                                   # numeric keyword in a set bag
    ({"sea": True, "1": "sea"}, ["sea"]),                         # duplicates removed
])
def test_keywords_shapes(subject, expected):
    assert compat._photo(_asset(subject)).keywords == expected


def test_keywords_never_contain_bools():
    kws = compat._photo(_asset({"a": True, "b": True})).keywords
    assert all(isinstance(k, str) for k in kws)


class _Photo:
    def __init__(self, i):
        self.id, self.file, self.changed_at = f"{i:032x}", f"IMG_{i}.jpg", "t"


def test_fetch_renditions_retry_clears_error_and_writes_index(tmp_path, monkeypatch):
    calls = {}

    def rendition(asset_id, size):
        calls[asset_id] = calls.get(asset_id, 0) + 1
        if asset_id.endswith("1") and calls[asset_id] == 1:
            raise compat.LrError("transient")
        if asset_id.endswith("2"):
            raise compat.LrError("permanent")
        return b"jpeg"

    monkeypatch.setattr(compat.lr, "rendition", rendition)
    monkeypatch.setattr(compat.time, "sleep", lambda s: None)
    photos = [_Photo(0), _Photo(1), _Photo(2)]
    res = compat.fetch_renditions(photos, tmp_path, size="640", workers=2)

    assert set(res["errors"]) == {photos[2].id}       # the retried download is not an error
    assert res["downloaded"] == 2
    assert calls[photos[2].id] == 3
    index = json.loads((tmp_path / "index.json").read_text())
    assert set(index) == {photos[0].id, photos[1].id}


def test_fetch_renditions_index_survives_interruption(tmp_path, monkeypatch):
    def rendition(asset_id, size):
        if asset_id.endswith("1"):
            raise KeyboardInterrupt
        return b"jpeg"

    monkeypatch.setattr(compat.lr, "rendition", rendition)
    photos = [_Photo(0), _Photo(1)]
    with pytest.raises(KeyboardInterrupt):
        compat.fetch_renditions(photos, tmp_path, size="640", workers=1)
    index = json.loads((tmp_path / "index.json").read_text())
    assert set(index) == {photos[0].id}               # first download is recorded, not refetched next run


def test_fetch_renditions_never_leaves_truncated_jpeg(tmp_path, monkeypatch):
    photo = _Photo(0)
    final = tmp_path / f"{photo.id}.jpg"
    final.write_bytes(b"old complete jpeg")
    monkeypatch.setattr(compat.lr, "rendition", lambda asset_id, size: b"new jpeg")
    real_replace = compat.Path.replace

    def dying_replace(self, target):  # process killed after the temp file is written, before the rename
        if str(target).endswith(".jpg"):
            raise KeyboardInterrupt
        return real_replace(self, target)

    monkeypatch.setattr(compat.Path, "replace", dying_replace)
    with pytest.raises(KeyboardInterrupt):
        compat.fetch_renditions([photo], tmp_path, size="640", force=True, workers=1)
    assert final.read_bytes() == b"old complete jpeg"
    assert not list(tmp_path.glob("*.part")) and not list(tmp_path.glob(".*.part"))   # temp cleaned up

    monkeypatch.setattr(compat.Path, "replace", real_replace)
    compat.fetch_renditions([photo], tmp_path, size="640", force=True, workers=1)
    assert final.read_bytes() == b"new jpeg"


def test_fetch_renditions_duplicate_ids_download_once(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(compat.lr, "rendition", lambda asset_id, size: calls.append(asset_id) or b"jpeg")
    photo = _Photo(0)
    res = compat.fetch_renditions([photo, photo], tmp_path, size="640", workers=2)
    assert res == {"downloaded": 1, "skipped": 0, "errors": {}}
    assert calls == [photo.id]


def test_fetch_renditions_concurrent_writers_of_one_photo(tmp_path, monkeypatch):
    """Two runs fetching the same book at once (CLI + MCP): both reach the rename together."""
    monkeypatch.setattr(compat.lr, "rendition", lambda asset_id, size: b"jpeg")
    real_replace = compat.Path.replace
    both_written = threading.Barrier(2, timeout=5)

    def replace(self, target):
        if str(target).endswith(".jpg"):
            both_written.wait()  # both writers have their temp file before either renames
        return real_replace(self, target)

    monkeypatch.setattr(compat.Path, "replace", replace)
    results, failures = [], []

    def run():
        try:
            results.append(compat.fetch_renditions([_Photo(0)], tmp_path, size="640", force=True, workers=1))
        except Exception as e:  # noqa: BLE001 - surface any worker failure in the assertion below
            failures.append(e)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert failures == []
    assert [r["errors"] for r in results] == [{}, {}]
    assert (tmp_path / f"{_Photo(0).id}.jpg").read_bytes() == b"jpeg"
    assert not list(tmp_path.glob(".*.part"))


@pytest.mark.parametrize("status, expected", [
    (404, "album " + "d" * 32 + " not found in the Lightroom catalog"),
    (500, "returned 500"),                    # anything else keeps lr-mcp's message
])
def test_album_info_not_found(monkeypatch, status, expected):
    """A well-formed id of no album: a sentence, not lr-mcp's raw response body (a dict repr)."""
    from lr_mcp import client as lr
    monkeypatch.setattr(lr, "catalog_path", lambda: "/v2/catalogs/c0ffee/")
    monkeypatch.setattr(lr, "request", lambda method, path, **k: (
        status, b'while (1) {}\n{"code":1000,"description":"Resource not found"}'))
    with pytest.raises(compat.LrError) as exc:
        compat.album_info("d" * 32)
    assert expected in str(exc.value)
    assert ("{'code'" in str(exc.value)) == (status != 404)
