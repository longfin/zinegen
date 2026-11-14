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
        seen: set = set()        # objects already checked (resources are often shared between pages)
        intent_cmyk = bool(oi) and int(oi[0].get("/DestOutputProfile", {}).get("/N", 0)) == 4
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
            elif tb is not None:
                t, b = [float(v) for v in tb], [float(v) for v in bb]
                least = min(t[0] - b[0], t[1] - b[1], b[2] - t[2], b[3] - t[3])
                if least < book.bleed * PT - 0.6:
                    issues.append(Issue("BLEED_SMALL", f"BleedBox is {least / PT:.2f} mm beyond the trim on its "
                                        f"narrowest side, smaller than the {book.bleed} mm bleed", page=n))
            issues += _check_content(page, n, std, intent_cmyk, seen)
    rep_file = book.out_dir / "report.json"
    if rep_file.exists():
        for it in json.loads(rep_file.read_text()).get("issues", []):
            issues.append(Issue(**it))
    result = {"version": CHECK_VERSION, "ok": not has_errors(issues), "standard": std, "info": info,
              "issues": [i.to_dict() for i in issues]}
    (book.out_dir / "check.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


# Colour space families PDF/X-1a allows; Separation/DeviceN/Indexed are checked through their alternate/base.
X1A_SPACES = ("/DeviceCMYK", "/DeviceGray", "/Separation", "/DeviceN", "/Indexed", "/Pattern")


def _families(cs) -> list[str]:
    """Every colour space family a colour space uses: itself, a Separation/DeviceN alternate, an Indexed base."""
    if isinstance(cs, pikepdf.Array) and len(cs):
        fam = str(cs[0])
        if fam in ("/Separation", "/DeviceN") and len(cs) > 2:
            return [fam, *_families(cs[2])]
        if fam == "/Indexed" and len(cs) > 1:
            return [fam, *_families(cs[1])]
        return [fam]
    return [str(cs)] if cs is not None else []


def _bad_space(families: list[str], std: str, intent_cmyk: bool) -> str | None:
    """Why a colour space is not allowed on this standard, or None."""
    if std == "pdf/x-1a":
        bad = [f for f in families if f not in X1A_SPACES]
        return f"{bad[0]} (PDF/X-1a allows CMYK, Gray, Separation and DeviceN only)" if bad else None
    if "/DeviceRGB" in families and intent_cmyk:
        return "/DeviceRGB (PDF/X-4 with a CMYK output intent needs RGB to be ICC-tagged)"
    return None


def _check_content(page, n: int, std: str, intent_cmyk: bool, seen: set) -> list[Issue]:
    """Check a page and everything its resources reach: nested Form XObjects (and tiling patterns), their
    transparency groups, ExtGStates, images, fonts and the colour operators in every content stream.
    WeasyPrint's own crop marks were a Form with a transparency group, an ExtGState with `ca 0` and DeviceRGB
    strokes, all invisible to a check that only looked at the page's own resources."""
    issues: list[Issue] = []
    tlevel = "error" if std == "pdf/x-1a" else "warning"

    def once(obj) -> bool:
        """True the first time an indirect object is seen; direct objects are always checked."""
        if not obj.is_indirect:
            return True
        if obj.objgen in seen:
            return False
        seen.add(obj.objgen)
        return True

    def space(cs, what: str):
        why = _bad_space(_families(cs), std, intent_cmyk)
        if why:
            issues.append(Issue("VECTOR_COLORSPACE", f"{what} is {why}", page=n))

    def resources(res, where: str):
        for k, g in (res.get("/ExtGState") or {}).items():
            if not once(g):
                continue
            smask = g.get("/SMask")
            if (float(g.get("/CA", 1)) < 1 or float(g.get("/ca", 1)) < 1
                    or str(g.get("/BM", "/Normal")) not in ("/Normal", "/Compatible")
                    or (smask is not None and str(smask) != "/None")):
                issues.append(Issue("TRANSPARENCY", f"transparency in ExtGState {k}{where}", page=n, level=tlevel))
        for k, cs in (res.get("/ColorSpace") or {}).items():
            space(cs, f"colour space {k}{where}")
        for k, sh in (res.get("/Shading") or {}).items():
            space(sh.get("/ColorSpace"), f"shading {k}{where}")
        for fo in (res.get("/Font") or {}).values():
            if not once(fo):
                continue
            fd = fo.get("/DescendantFonts")
            d = (fd[0] if fd else fo).get("/FontDescriptor")
            if not d or not any(k in d for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                issues.append(Issue("FONT_NOT_EMBEDDED", f"font {fo.get('/BaseFont')} not embedded", page=n))
        for name, x in (res.get("/XObject") or {}).items():
            if not once(x):
                continue
            sub = str(x.get("/Subtype"))
            if sub == "/Image":
                image(name, x)
            elif sub == "/Form":
                form(x, f" in form XObject {name}")
        for name, pat in (res.get("/Pattern") or {}).items():
            if not once(pat):
                continue
            if int(pat.get("/PatternType", 0)) == 1:
                form(pat, f" in pattern {name}")
            elif "/Shading" in pat:
                space(pat.Shading.get("/ColorSpace"), f"shading in pattern {name}")

    def image(name, x):
        fam = _families(x.get("/ColorSpace"))
        if std == "pdf/x-1a" and not x.get("/ImageMask", False) and (not fam or any(f not in X1A_SPACES for f in fam)):
            issues.append(Issue("IMAGE_COLORSPACE", f"image {name} is {fam[0] if fam else 'without a colour space'}; "
                                "PDF/X-1a allows CMYK/Gray only", page=n))
        if std == "pdf/x-4" and fam[:1] == ["/DeviceRGB"]:
            issues.append(Issue("IMAGE_COLORSPACE", f"image {name} is untagged DeviceRGB; PDF/X-4 needs ICCBased RGB", page=n))
        if "/SMask" in x and std == "pdf/x-1a":
            issues.append(Issue("IMAGE_SMASK", f"image {name} has a soft mask (transparency)", page=n))

    def form(x, where: str):
        group = x.get("/Group")
        if group is not None and str(group.get("/S")) == "/Transparency":
            issues.append(Issue("TRANSPARENCY", f"transparency group{where}", page=n, level=tlevel))
        res = x.get("/Resources")
        if res is not None:
            resources(res, where)
        operators(x, where)

    def operators(obj, where: str):
        """DeviceRGB set directly by colour operators (rg/RG, or cs/CS /DeviceRGB) in a content stream."""
        try:
            ops = pikepdf.parse_content_stream(obj)
        except pikepdf.PdfError:
            return
        rgb = sum(1 for operands, op in ops
                  if str(op) in ("rg", "RG")
                  or (str(op) in ("cs", "CS") and operands and str(operands[0]) == "/DeviceRGB"))
        why = _bad_space(["/DeviceRGB"], std, intent_cmyk) if rgb else None
        if why:
            issues.append(Issue("VECTOR_COLORSPACE", f"{rgb} DeviceRGB colour operator(s) "
                                f"{where.strip() or 'in the page content'}: {why}", page=n))

    group = page.obj.get("/Group")
    if group is not None and str(group.get("/S")) == "/Transparency":
        issues.append(Issue("TRANSPARENCY", "page has a transparency group", page=n, level=tlevel))
    resources(page.Resources, "")
    operators(page.obj, "")
    return issues
