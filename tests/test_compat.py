"""lr/compat.py helpers, without the network (lr_mcp.client is stubbed where needed)."""
import json

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
