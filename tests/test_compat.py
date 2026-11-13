"""lr/compat.py helpers, without the network (lr_mcp.client is stubbed where needed)."""

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
