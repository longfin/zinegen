import pytest

from zinegen import presets

# Keys and sizes released in 0.1.0 are part of the frozen book.yaml format.
FROZEN = {
    "A4": (210, 297), "A4L": (297, 210), "A5": (148, 210), "A5L": (210, 148), "B5": (176, 250),
    "B5K": (182, 257), "SQ210": (210, 210), "SQ180": (180, 180), "46": (127, 188), "SIN46": (128, 188),
    "CROWN": (152, 225), "8x10": (203.2, 254), "10x8": (254, 203.2), "7x7": (177.8, 177.8),
}


@pytest.mark.parametrize("key, size", FROZEN.items())
def test_frozen_presets_keep_their_size(key, size):
    assert presets.resolve_trim(key) == size


def test_korean_presets_are_named_by_size():
    assert presets.resolve_trim("SINGUK") == (152, 225)
    assert presets.resolve_trim("CROWN176") == (176, 248)
    assert presets.TRIM_PRESETS["SINGUK"][2] == "신국판"
    assert presets.TRIM_PRESETS["CROWN176"][2] == "크라운판"
    assert "신국판" in presets.TRIM_PRESETS["CROWN"][2]       # legacy key, label describes its real size
    assert "신사륙판" in presets.TRIM_PRESETS["SIN46"][2]


@pytest.mark.parametrize("name, size", [("8x10", (203.2, 254)), ("8X10", (203.2, 254)), ("sin46", (128, 188))])
def test_preset_lookup_is_case_insensitive(name, size):
    assert presets.resolve_trim(name) == size
    assert presets.resolve_trim({"preset": name}) == size


def test_unknown_preset():
    with pytest.raises(ValueError, match="unknown trim preset"):
        presets.resolve_trim("Z9")
