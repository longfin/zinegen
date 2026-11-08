"""Low-resolution spread proofs (PNG) rendered with Ghostscript, for agents and the review GUI."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from .spec import Book


def render_pages(pdf: Path, dest: Path, dpi: int = 50) -> list[Path]:
    gs = shutil.which("gs")
    if not gs:
        raise RuntimeError("Ghostscript (gs) is not installed; `brew install ghostscript`")
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("page-*.png"):
        old.unlink()
    subprocess.run([gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=png16m", f"-r{dpi}",
                    "-dUseTrimBox", "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4",
                    f"-sOutputFile={dest / 'page-%03d.png'}", str(pdf)], check=True, capture_output=True)
    return sorted(dest.glob("page-*.png"))


def spreads(pages: list[Path], dest: Path, gap: int = 0) -> list[Path]:
    """Page 1 alone on the right, then (2,3), (4,5) ...; a trailing even page alone on the left."""
    for old in dest.glob("spread-*.png"):
        old.unlink()
    out = []
    groups: list[tuple[Path | None, Path | None]] = [(None, pages[0])] if pages else []
    rest = pages[1:]
    for i in range(0, len(rest), 2):
        groups.append((rest[i], rest[i + 1] if i + 1 < len(rest) else None))
    for n, (l, r) in enumerate(groups, 1):
        sample = Image.open(l or r)
        w, h = sample.size
        canvas = Image.new("RGB", (2 * w + gap + 40, h + 40), (90, 90, 90))
        draw = ImageDraw.Draw(canvas)
        for img, x in ((l, 20), (r, 20 + w + gap)):
            if img is not None:
                canvas.paste(Image.open(img), (x, 20))
            else:
                draw.rectangle([x, 20, x + w - 1, 20 + h - 1], outline=(120, 120, 120))
        draw.line([(20 + w, 20), (20 + w, 20 + h)], fill=(200, 60, 60), width=1)   # spine
        p = dest / f"spread-{n:02d}.png"
        canvas.save(p)
        out.append(p)
    return out


def proof(book: Book, dpi: int = 50, log=None) -> list[Path]:
    pdf = book.out_dir / "interior.pdf"
    if not pdf.exists():
        raise FileNotFoundError("render the book first (`zinegen render`)")
    pages = render_pages(pdf, book.proof_dir, dpi)
    out = spreads(pages, book.proof_dir)
    if log:
        log(f"{len(pages)} pages -> {len(out)} spreads in {book.proof_dir}")
    return out
