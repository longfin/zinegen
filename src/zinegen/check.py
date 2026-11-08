"""Structural PDF/X self-check with pikepdf. Not a substitute for a real preflight (Acrobat/callas),
but catches what print shops reject first: missing output intent, wrong boxes, RGB images, live
transparency, unembedded fonts, low resolution."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pikepdf

from .spec import Book

PT = 72 / 25.4


def check(book: Book) -> dict:
    pdf_path = book.out_dir / "interior.pdf"
    std = book.output["standard"]
    errors, warnings, info = [], [], {}
    with pikepdf.open(pdf_path) as pdf:
        info["pdf_version"] = pdf.pdf_version
        info["pages"] = len(pdf.pages)
        oi = pdf.Root.get("/OutputIntents")
        if not oi:
            errors.append("no OutputIntent")
        else:
            o = oi[0]
            info["output_intent"] = str(o.get("/OutputConditionIdentifier"))
            if "/DestOutputProfile" not in o:
                errors.append("OutputIntent has no DestOutputProfile")
        xmp = pdf.Root.get("/Metadata")
        xmp_text = xmp.read_bytes().decode("utf-8", "replace") if xmp else ""
        m = re.search(r"GTS_PDFXVersion>([^<]+)<", xmp_text)
        info["pdfx_version"] = m.group(1) if m else str(pdf.docinfo.get("/GTS_PDFXVersion", ""))
        if not info["pdfx_version"]:
            errors.append("no GTS_PDFXVersion")
        if std == "pdf/x-1a" and pdf.pdf_version > "1.4":
            errors.append(f"PDF/X-1a requires PDF 1.4 or lower, got {pdf.pdf_version}")
        exp_trim = (round(book.trim_w * PT, 1), round(book.trim_h * PT, 1))
        seen_images = set()
        for n, page in enumerate(pdf.pages, 1):
            tb = page.obj.get("/TrimBox")
            if tb is None:
                errors.append(f"page {n}: no TrimBox")
            else:
                w, h = round(float(tb[2]) - float(tb[0]), 1), round(float(tb[3]) - float(tb[1]), 1)
                if abs(w - exp_trim[0]) > 0.6 or abs(h - exp_trim[1]) > 0.6:
                    errors.append(f"page {n}: TrimBox {w}×{h} pt, expected {exp_trim}")
            bb = page.obj.get("/BleedBox")
            if bb is None:
                warnings.append(f"page {n}: no BleedBox")
            elif tb is not None and (float(tb[0]) - float(bb[0])) < book.bleed * PT - 0.6:
                errors.append(f"page {n}: bleed smaller than {book.bleed} mm")
            for name, x in page.images.items():
                if x.objgen in seen_images:
                    continue
                seen_images.add(x.objgen)
                cs = x.get("/ColorSpace")
                fam = str(cs[0]) if isinstance(cs, pikepdf.Array) else str(cs)
                if std == "pdf/x-1a" and fam not in ("/DeviceCMYK", "/DeviceGray", "/Separation", "/DeviceN", "/Indexed"):
                    errors.append(f"page {n}: image {name} is {fam}; PDF/X-1a allows CMYK/Gray only")
                if std == "pdf/x-4" and fam == "/DeviceRGB":
                    errors.append(f"page {n}: image {name} is untagged DeviceRGB; PDF/X-4 needs ICCBased RGB")
                if "/SMask" in x and std == "pdf/x-1a":
                    errors.append(f"page {n}: image {name} has a soft mask (transparency)")
            for k, g in (page.Resources.get("/ExtGState") or {}).items():
                if float(g.get("/CA", 1)) < 1 or float(g.get("/ca", 1)) < 1 or str(g.get("/BM", "/Normal")) not in ("/Normal", "/Compatible"):
                    (errors if std == "pdf/x-1a" else warnings).append(f"page {n}: transparency in ExtGState {k}")
            for fk, fo in (page.Resources.get("/Font") or {}).items():
                fd = fo.get("/DescendantFonts")
                d = (fd[0] if fd else fo).get("/FontDescriptor")
                if not d or not any(k in d for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                    errors.append(f"page {n}: font {fo.get('/BaseFont')} not embedded")
    rep_file = book.out_dir / "report.json"
    if rep_file.exists():
        rep = json.loads(rep_file.read_text())
        for im in rep.get("images", []):
            if im["level"] == "bad":
                errors.append(f"page {im['page']}: {im['dpi']} dpi (< 200)")
            elif im["level"] == "low":
                warnings.append(f"page {im['page']}: {im['dpi']} dpi (< 300)")
        warnings += [w for w in rep.get("warnings", []) if "dpi" not in w]
    result = {"ok": not errors, "standard": std, "errors": errors, "warnings": warnings, "info": info}
    (book.out_dir / "check.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result
