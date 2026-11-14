"""Shared test helpers.

CMYK press profiles are not redistributable, so the PDF/X-1a tests use whatever CMYK profile the machine has:
`ZINEGEN_TEST_CMYK_ICC` (a path), an installed press profile, or Ghostscript's `default_cmyk.icc` (installed
with ghostscript, which CI already needs for proofs). Without one they are skipped, unless
`ZINEGEN_REQUIRE_CMYK=1` (set on CI) turns the skip into a failure so the X-1a path cannot silently go untested.
"""
from __future__ import annotations

import atexit
import glob
import os
import shutil
import tempfile

# lr_mcp.client reads its state directory (.env with the Adobe credential, tokens) when it is first
# imported. Point it at an empty directory before any test can import it, so the suite never loads the
# developer's real sign-in.
LR_MCP_TEST_HOME = tempfile.mkdtemp(prefix="zinegen-test-lr-mcp-")
os.environ["LR_MCP_HOME"] = LR_MCP_TEST_HOME
atexit.register(shutil.rmtree, LR_MCP_TEST_HOME, ignore_errors=True)

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from zinegen import color  # noqa: E402

GS_CMYK_GLOBS = [
    "/usr/share/color/icc/ghostscript/default_cmyk.icc",            # Debian/Ubuntu (libgs-common)
    "/usr/share/ghostscript/*/iccprofiles/default_cmyk.icc",
    "/opt/homebrew/share/ghostscript/*/iccprofiles/default_cmyk.icc",
    "/usr/local/share/ghostscript/*/iccprofiles/default_cmyk.icc",
]


def find_cmyk_profile() -> Path | None:
    env = os.environ.get("ZINEGEN_TEST_CMYK_ICC")
    if env:
        return Path(env)
    for name in ("JapanColor2001Coated", "CoatedFOGRA39", "USWebCoatedSWOP"):
        p = color.find_profile(name)
        if p:
            return p
    for pattern in GS_CMYK_GLOBS:
        hits = sorted(glob.glob(pattern))
        if hits:
            return Path(hits[-1])
    return None


@pytest.fixture
def cmyk_icc() -> Path:
    p = find_cmyk_profile()
    if p is None:
        if os.environ.get("ZINEGEN_REQUIRE_CMYK"):
            pytest.fail("ZINEGEN_REQUIRE_CMYK is set but no CMYK profile was found (install ghostscript or set "
                        "ZINEGEN_TEST_CMYK_ICC)")
        pytest.skip("no CMYK profile (install ghostscript, or set ZINEGEN_TEST_CMYK_ICC)")
    assert color.profile_components(p) == ["c", "m", "y", "k"], p
    return p
