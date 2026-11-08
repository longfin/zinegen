"""Structural PDF/X self-check with pikepdf, written to out/check.json (format version 1).

Not a substitute for a real preflight (Acrobat/callas), but catches what print shops reject first:
missing output intent, wrong boxes, RGB images, live transparency, unembedded fonts, low resolution.
"""
from __future__ import annotations

import json
import re

import pikepdf

from .issues import Issue, has_errors
from .spec import Book

CHECK_VERSION = 1
PT = 72 / 25.4


def check(book: Book) -> dict:
    pdf_path = book.out_dir / "interior.pdf"
    std = book.output["standard"]
    issues: list[Issue] = []
    info = {}
    with pikepdf.open(pdf_path) as pdf:
        info["pdf_version"] = pdf.pdf_version
        info["pages"] = len(pdf.pages)
        oi = pdf.Root.get("/OutputIntents")
        if not oi:
            issues.append(Issue("NO_OUTPUT_INTENT", "no OutputIntent"))
        else:
            o = oi[0]
            info["output_intent"] = str(o.get("/OutputConditionIdentifier"))
            if "/DestOutputProfile" not in o:
                issues.append(Issue("NO_DEST_PROFILE", "OutputIntent has no DestOutputProfile"))
        xmp = pdf.Root.get("/Metadata")
        xmp_text = xmp.read_bytes().decode("utf-8", "replace") if xmp else ""
        m = re.search(r"GTS_PDFXVersion>([^<]+)<", xmp_text)
        info["pdfx_version"] = m.group(1) if m else str(pdf.docinfo.get("/GTS_PDFXVersion", ""))
        if not info["pdfx_version"]:
            issues.append(Issue("NO_PDFX_VERSION", "no GTS_PDFXVersion"))
        if std == "pdf/x-1a" and pdf.pdf_version > "1.4":
            issues.append(Issue("PDF_VERSION", f"PDF/X-1a requires PDF 1.4 or lower, got {pdf.pdf_version}"))
        exp_trim = (round(book.trim_w * PT, 1), round(book.trim_h * PT, 1))
        seen_images = set()
        for n, page in enumerate(pdf.pages, 1):
            tb = page.obj.get("/TrimBox")
            if tb is None:
                issues.append(Issue("NO_TRIMBOX", "no TrimBox", page=n))
            else:
                w, h = round(float(tb[2]) - float(tb[0]), 1), round(float(tb[3]) - float(tb[1]), 1)
                if abs(w - exp_trim[0]) > 0.6 or abs(h - exp_trim[1]) > 0.6:
                    issues.append(Issue("TRIMBOX_SIZE", f"TrimBox {w}×{h} pt, expected {exp_trim}", page=n))
            bb = page.obj.get("/BleedBox")
            if bb is None:
                issues.append(Issue("NO_BLEEDBOX", "no BleedBox", page=n))
            elif tb is not None and (float(tb[0]) - float(bb[0])) < book.bleed * PT - 0.6:
                issues.append(Issue("BLEED_SMALL", f"bleed smaller than {book.bleed} mm", page=n))
            for name, x in page.images.items():
                if x.objgen in seen_images:
                    continue
                seen_images.add(x.objgen)
                cs = x.get("/ColorSpace")
                fam = str(cs[0]) if isinstance(cs, pikepdf.Array) else str(cs)
                if std == "pdf/x-1a" and fam not in ("/DeviceCMYK", "/DeviceGray", "/Separation", "/DeviceN", "/Indexed"):
                    issues.append(Issue("IMAGE_COLORSPACE", f"image {name} is {fam}; PDF/X-1a allows CMYK/Gray only", page=n))
                if std == "pdf/x-4" and fam == "/DeviceRGB":
                    issues.append(Issue("IMAGE_COLORSPACE", f"image {name} is untagged DeviceRGB; PDF/X-4 needs ICCBased RGB", page=n))
                if "/SMask" in x and std == "pdf/x-1a":
                    issues.append(Issue("IMAGE_SMASK", f"image {name} has a soft mask (transparency)", page=n))
            for k, g in (page.Resources.get("/ExtGState") or {}).items():
                if float(g.get("/CA", 1)) < 1 or float(g.get("/ca", 1)) < 1 or str(g.get("/BM", "/Normal")) not in ("/Normal", "/Compatible"):
                    issues.append(Issue("TRANSPARENCY", f"transparency in ExtGState {k}", page=n,
                                        level="error" if std == "pdf/x-1a" else "warning"))
            for fo in (page.Resources.get("/Font") or {}).values():
                fd = fo.get("/DescendantFonts")
                d = (fd[0] if fd else fo).get("/FontDescriptor")
                if not d or not any(k in d for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                    issues.append(Issue("FONT_NOT_EMBEDDED", f"font {fo.get('/BaseFont')} not embedded", page=n))
    rep_file = book.out_dir / "report.json"
    if rep_file.exists():
        for it in json.loads(rep_file.read_text()).get("issues", []):
            issues.append(Issue(**it))
    result = {"version": CHECK_VERSION, "ok": not has_errors(issues), "standard": std, "info": info,
              "issues": [i.to_dict() for i in issues]}
    (book.out_dir / "check.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result
