"""Structured issues shared by the spec loader, layout report and PDF check (report.json / check.json)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

# code -> default level. Levels: "error" blocks delivery, "warning" is reported, "info" is advisory.
CODES = {
    # spec / layout
    "SPREAD_MOVED": "info",
    "PAGES_PADDED": "info",
    "LOW_DPI": "warning",
    "BAD_DPI": "error",
    "GUTTER_CROSSING": "warning",
    "NO_ROOM": "error",
    # render
    "NO_OUTPUT_PROFILE": "error",
    "DPI_ABOVE_NEED": "info",
    "ORIENTATION_REENCODED": "warning",
    # pdf check
    "NO_OUTPUT_INTENT": "error",
    "NO_DEST_PROFILE": "error",
    "NO_PDFX_VERSION": "error",
    "PDF_VERSION": "error",
    "NO_TRIMBOX": "error",
    "TRIMBOX_SIZE": "error",
    "NO_BLEEDBOX": "warning",
    "BLEED_SMALL": "error",
    "IMAGE_COLORSPACE": "error",
    "IMAGE_SMASK": "error",
    "TRANSPARENCY": "error",
    "FONT_NOT_EMBEDDED": "error",
}


@dataclass
class Issue:
    code: str
    message: str
    level: str = ""
    page: int | None = None
    photo: str | None = None

    def __post_init__(self):
        if self.code not in CODES:
            raise ValueError(f"unknown issue code {self.code}")
        if not self.level:
            self.level = CODES[self.code]

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}

    def __str__(self):
        where = f"page {self.page}: " if self.page else ""
        return f"[{self.level}] {self.code}: {where}{self.message}"


def has_errors(issues: list[Issue]) -> bool:
    return any(i.level == "error" for i in issues)
