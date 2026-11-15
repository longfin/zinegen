"""lr/compat.py helpers, without the network (lr_mcp.client is stubbed where needed)."""
import json
import threading

import pytest

from zinegen.lr import compat


def _asset(subject):
    dc = {} if subject is None else {"subject": subject}
    return {"id": "a" * 32, "subtype": "image", "payload": {"xmp": {"dc": dc}}}


@pytest.mark.parametrize("subject, expected", [
    ({"sea": True, "winter": True}, ["sea", "winter"]),        # shape the Partner API actually sends
    ({"sea": True, "dropped": False}, ["sea"]),
    (["sea", "winter"], ["sea", "winter"]),
    ("sea", ["sea"]),
    ({}, []),
    (None, []),
    ({"sea": 1}, ["sea"]),                                        # truthy non-bool value
    ({"2026": True}, ["2026"]),                                   # numeric keyword in a set bag
])
def test_keywords_are_strings(subject, expected):
    """Keywords are lr_mcp.catalog's; 0.1.0 wrote the bag's `true` values into photos.json instead."""
    kws = compat._photo(_asset(subject)).keywords
    assert kws == expected and all(isinstance(k, str) for k in kws)


def test_photo_fields_from_asset_summary():
    asset = {"id": "a" * 32, "subtype": "image", "payload": {
        "captureDate": "2026-11-07T14:08:16", "changedAtTime": "c", "userUpdated": "u",
        "importSource": {"fileName": "R0010712.DNG", "originalWidth": 6192, "originalHeight": 4128},
        "develop": {"croppedWidth": 4000, "croppedHeight": 3000, "userOrientation": 6, "userUpdated": "d"},
        "ratings": {"x": {"rating": 4, "date": "2026-11-08"}}, "reviews": {"x": {"flag": "pick", "date": "2026-11-08"}},
        "xmp": {"dc": {"title": {"x-default": " Maple "}, "description": "first line\nsecond"},
                "tiff": {"Make": "RICOH", "Model": "GR IV"}, "aux": {"Lens": "18.3mm F2.8"}}}}
    p = compat._photo(asset, "V--3")
    assert p.to_dict() == {
        "id": "a" * 32, "file": "R0010712.DNG", "captured": "2026-11-07T14:08:16", "order": "V--3",
        "title": "Maple", "caption": "first line\nsecond", "keywords": [], "rating": 4, "flag": "pick",
        "camera": "RICOH GR IV", "lens": "18.3mm F2.8", "original": (6192, 4128), "cropped": (4000, 3000),
        "orientation": 6, "changed_at": "c", "edited_at": "d"}
    # the keys photos.json (version 1) has always had, in the same order
    assert list(p.to_dict()) == ["id", "file", "captured", "order", "title", "caption", "keywords", "rating", "flag",
                                 "camera", "lens", "original", "cropped", "orientation", "changed_at", "edited_at"]
    assert compat._photo({"id": "b" * 32}).order is None      # looked up by id, outside any album


def _error(message, status=None, retryable=False):
    e = compat.LrError(message)
    e.status, e.retryable = status, retryable
    return e


class _Photo:
    def __init__(self, i):
        self.id, self.file, self.changed_at, self.edited_at = f"{i:032x}", f"IMG_{i}.jpg", "t", "e"


def test_fetch_renditions_retry_clears_error_and_writes_index(tmp_path, monkeypatch):
    calls = {}

    def rendition(asset_id, size):
        calls[asset_id] = calls.get(asset_id, 0) + 1
        if asset_id.endswith("1") and calls[asset_id] == 1:   # generated, not ready by the end of polling
            raise _error(f"Rendition {size} for {asset_id} failed (404): None", 404, retryable=True)
        if asset_id.endswith("2"):
            raise _error(f"Rendition {size} for {asset_id} failed (500): boom", 500)
        return b"jpeg"

    monkeypatch.setattr(compat.lr, "rendition", rendition)
    monkeypatch.setattr(compat.time, "sleep", lambda s: None)
    photos = [_Photo(0), _Photo(1), _Photo(2)]
    res = compat.fetch_renditions(photos, tmp_path, size="fullsize", workers=2)

    assert set(res["errors"]) == {photos[2].id}       # the retried download is not an error
    assert res["downloaded"] == 2
    assert calls == {photos[0].id: 1, photos[1].id: 2, photos[2].id: 1}
    index = json.loads((tmp_path / "index.json").read_text())
    assert set(index) == {photos[0].id, photos[1].id}


@pytest.mark.parametrize("retryable, attempts", [(False, 1), (True, 2)])
def test_fetch_renditions_attempts_follow_retryable(tmp_path, monkeypatch, retryable, attempts):
    """One more call only for what lr-mcp marks retryable; it has retried everything else itself."""
    calls = []

    def rendition(asset_id, size):
        calls.append(asset_id)
        raise _error(f"Rendition {size} for {asset_id} failed (404): None", 404, retryable)

    monkeypatch.setattr(compat.lr, "rendition", rendition)
    monkeypatch.setattr(compat.time, "sleep", lambda s: None)
    res = compat.fetch_renditions([_Photo(0)], tmp_path, size="fullsize", workers=1)
    assert len(calls) == attempts and set(res["errors"]) == {_Photo(0).id}


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


@pytest.mark.parametrize("size, get_status, post_status, retry", [
    ("fullsize", 404, 202, True),    # generation requested, still 404 on every poll
    ("2560", 404, 503, True),        # the generate POST (sent once by lr-mcp) refused as busy
    ("fullsize", 404, 403, False),   # generate refused for good
    ("fullsize", 403, None, False),  # the GET itself is forbidden
    ("fullsize", 503, None, False),  # lr-mcp's request() retries these GETs itself (stubbed out here)
    ("640", 404, None, False),       # a stored size: the photo is gone
])
def test_lr_mcp_marks_what_fetch_retries(monkeypatch, size, get_status, post_status, retry):
    """Contract with the real lr_mcp.client.rendition: `retryable` is what fetch_renditions goes by."""
    from lr_mcp import client as lr
    calls = []

    def request(method, path, **k):
        calls.append(method)
        if method == "POST":
            return post_status, b'while (1) {}\n{"code":1000,"description":"busy"}'
        return get_status, b'while (1) {}\n{"code":1000,"description":"Resource not found"}'

    monkeypatch.setattr(lr, "catalog_path", lambda: "/v2/catalogs/c0ffee/")
    monkeypatch.setattr(lr, "request", request)
    monkeypatch.setattr(lr.time, "sleep", lambda s: None)
    with pytest.raises(compat.LrError) as exc:
        lr.rendition("a" * 32, size)
    assert exc.value.retryable is retry
    if post_status == 202:
        assert calls.count("POST") == 1 and calls.count("GET") > 1   # it really polled


def test_lr_mcp_state_is_isolated():
    """conftest points lr-mcp at a temporary home, so the real sign-in is never loaded."""
    import os
    from pathlib import Path

    from lr_mcp import client as lr
    assert lr.HOME == Path(os.environ["LR_MCP_HOME"]) and lr.HOME.name.startswith("zinegen-test-lr-mcp-")
    assert lr.HOME != lr._default_home()   # compares paths only; the real directory is never read
    assert not (lr.HOME / ".env").exists() and not lr.TOKEN_FILE.exists()


def _edited_photo(changed, user=None, develop=None):
    """A photo as the Partner API describes it: changedAtTime moves on any change, ratings included."""
    payload = {"changedAtTime": changed, "importSource": {"fileName": "IMG_0.jpg"}}
    if user:
        payload["userUpdated"] = user
    if develop:
        payload["develop"] = {"userUpdated": develop}
    return compat._photo({"id": "a" * 32, "subtype": "image", "payload": payload})


@pytest.mark.parametrize("payload, expected", [
    ({"changedAtTime": "c", "userUpdated": "u", "develop": {"userUpdated": "d"}}, "d"),
    ({"changedAtTime": "c", "userUpdated": "u", "develop": {}}, "u"),
    ({"changedAtTime": "c"}, "c"),
    ({}, None),
])
def test_edited_at_prefers_develop_time(payload, expected):
    p = compat._photo({"id": "a" * 32, "subtype": "image", "payload": payload})
    assert p.edited_at == expected
    assert p.changed_at == (payload.get("changedAtTime") or payload.get("userUpdated"))   # unchanged meaning
    assert p.to_dict()["edited_at"] == expected                                          # in photos.json


@pytest.fixture
def renditions(monkeypatch):
    calls = []
    monkeypatch.setattr(compat.lr, "rendition", lambda asset_id, size: calls.append(asset_id) or b"jpeg")
    return calls


def test_rating_change_does_not_download_again(tmp_path, renditions):
    compat.fetch_renditions([_edited_photo("c1", "u1", "d1")], tmp_path, size="640")
    # a rating or flag edit moves changedAtTime and userUpdated, not develop.userUpdated
    res = compat.fetch_renditions([_edited_photo("c2", "u2", "d1")], tmp_path, size="640")
    assert res == {"downloaded": 0, "skipped": 1, "errors": {}} and len(renditions) == 1


def test_develop_change_downloads_again(tmp_path, renditions):
    compat.fetch_renditions([_edited_photo("c1", "u1", "d1")], tmp_path, size="640")
    res = compat.fetch_renditions([_edited_photo("c2", "u2", "d2")], tmp_path, size="640")
    assert res["downloaded"] == 1 and len(renditions) == 2
    entry = json.loads((tmp_path / "index.json").read_text())["a" * 32]
    assert entry["edited_at"] == "d2" and entry["changed_at"] == "c2"


def _old_index(tmp_path, changed_at):
    """cache/index.json as zinegen 0.2.0 wrote it: no edited_at."""
    (tmp_path / f"{'a' * 32}.jpg").write_bytes(b"old jpeg")
    (tmp_path / "index.json").write_text(json.dumps({"a" * 32: {
        "size": "640", "changed_at": changed_at, "bytes": 8, "file": "IMG_0.jpg", "fetched_at": 0}}))


def test_old_index_entry_is_honoured_and_upgraded(tmp_path, renditions):
    _old_index(tmp_path, "c1")
    res = compat.fetch_renditions([_edited_photo("c1", "u1", "d1")], tmp_path, size="640")
    assert res == {"downloaded": 0, "skipped": 1, "errors": {}} and renditions == []
    entry = json.loads((tmp_path / "index.json").read_text())["a" * 32]
    assert entry["edited_at"] == "d1" and entry["fetched_at"] == 0
    # from now on a rating change is recognised as such
    assert compat.fetch_renditions([_edited_photo("c2", "u2", "d1")], tmp_path, size="640")["skipped"] == 1
    assert renditions == []


def test_unknown_edit_time_downloads_again(tmp_path, renditions):
    """No timestamp at all from Lightroom: the cache cannot tell, so download (as lr-mcp's cache does)."""
    bare = compat._photo({"id": "a" * 32, "subtype": "image", "payload": {}})
    assert bare.edited_at is None and bare.changed_at is None
    compat.fetch_renditions([bare], tmp_path, size="640")
    assert compat.fetch_renditions([bare], tmp_path, size="640")["downloaded"] == 1
    _old_index(tmp_path, None)                          # a 0.2.0 entry recorded without a time either
    assert compat.fetch_renditions([bare], tmp_path, size="640")["downloaded"] == 1
    assert len(renditions) == 3
    # any timestamp is enough to cache: a photo with only changedAtTime is fetched once
    dated = _edited_photo("c1")
    compat.fetch_renditions([dated], tmp_path, size="640")
    assert compat.fetch_renditions([dated], tmp_path, size="640")["skipped"] == 1 and len(renditions) == 4


def test_old_index_entry_with_another_change_downloads(tmp_path, renditions):
    # changed since the 0.2.0 fetch; the old entry cannot tell whether the edit was a rating, so fetch once
    _old_index(tmp_path, "c0")
    res = compat.fetch_renditions([_edited_photo("c1", "u1", "d1")], tmp_path, size="640")
    assert res["downloaded"] == 1 and (tmp_path / f"{'a' * 32}.jpg").read_bytes() == b"jpeg"


@pytest.fixture
def album(monkeypatch):
    """A fake Lightroom album: set `album["sort"]` and `album["rows"]` (album-asset resources)."""
    from lr_mcp import catalog
    from lr_mcp import client as lr
    state = {"sort": "customAsc", "rows": [], "id": "d" * 32}
    catalog._album_cache.clear()      # lr-mcp keeps an album for a minute
    monkeypatch.setattr(lr, "catalog_path", lambda: "/v2/catalogs/c0ffee/")
    monkeypatch.setattr(lr, "get_json", lambda path, **k: {
        "id": state["id"], "subtype": "collection", "payload": {"name": "Autumn", "assetSortOrder": state["sort"]}})
    monkeypatch.setattr(lr, "paginate", lambda path: iter(state["rows"]))
    yield state
    catalog._album_cache.clear()


def _row(n, captured, order=None, subtype="image"):
    row = {"asset": {"id": f"{n:032x}", "subtype": subtype, "payload": {"captureDate": captured}}}
    if order is not None:
        row["payload"] = {"order": order}
    return row


@pytest.mark.parametrize("sort, expected", [
    ("customAsc", [3, 2, 1, 4]),          # by order key; the photo without one goes last
    ("captureDateAsc", [1, 2, 3, 4]),
    ("captureDateDesc", [4, 3, 2, 1]),
    (None, [1, 2, 3, 4]),                 # an album that has no sort mode
])
def test_album_assets_in_album_order(album, sort, expected):
    album["sort"] = sort
    album["rows"] = [_row(2, "2026-01-02", "V--2"), _row(4, "2026-01-04"), _row(1, "2026-01-01", "V--3"),
                     _row(3, "2026-01-03", "V--1"), _row(9, "2026-01-09", "V--0", subtype="video")]
    photos = compat.album_assets(album["id"])
    assert [int(p.id, 16) for p in photos] == expected
    assert {int(p.id, 16): p.order for p in photos} == {1: "V--3", 2: "V--2", 3: "V--1", 4: None}
    assert compat.album_info(album["id"]) == {"id": album["id"], "name": "Autumn", "parent": None,
                                              "subtype": "collection", "sort": sort or "captureDateAsc"}
    assert len(compat.album_assets(album["id"], images_only=False)) == 5


def test_album_deleted_while_listing(album, monkeypatch):
    """The album answers, then its asset list is gone: the same sentence as an unknown id."""
    from lr_mcp import client as lr

    def gone(path):
        raise _error("GET /v2/catalogs/c0ffee/albums/x/assets returned 404: {'code': 1000}", 404)
        yield

    monkeypatch.setattr(lr, "paginate", gone)
    with pytest.raises(compat.LrError, match="not found in the Lightroom catalog") as exc:
        compat.album_assets(album["id"])
    assert exc.value.status == 404 and "lr_list_albums" not in str(exc.value)


@pytest.mark.parametrize("status, expected", [
    (404, "album " + "d" * 32 + " not found in the Lightroom catalog"),
    (500, "returned 500"),                    # anything else keeps lr-mcp's message
])
def test_album_info_not_found(monkeypatch, status, expected):
    """A well-formed id of no album: a sentence, not lr-mcp's raw response body (a dict repr)."""
    from lr_mcp import catalog
    from lr_mcp import client as lr
    catalog._album_cache.clear()
    monkeypatch.setattr(lr, "catalog_path", lambda: "/v2/catalogs/c0ffee/")
    monkeypatch.setattr(lr, "request", lambda method, path, **k: (
        status, b'while (1) {}\n{"code":1000,"description":"Resource not found"}'))
    with pytest.raises(compat.LrError) as exc:
        compat.album_info("d" * 32)
    assert expected in str(exc.value)
    assert ("{'code'" in str(exc.value)) == (status != 404)
    assert exc.value.status == status and "lr_list_albums" not in str(exc.value)
