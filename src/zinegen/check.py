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
        issues += check_content(pdf, std)
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
# Abbreviated colour space names an inline image (BI … ID … EI) may use.
INLINE_SPACES = {"/G": "/DeviceGray", "/RGB": "/DeviceRGB", "/CMYK": "/DeviceCMYK", "/I": "/Indexed"}
# Colour space families that are never a resource name.
_FAMILIES = {"/DeviceGray", "/DeviceRGB", "/DeviceCMYK", "/Indexed", "/Separation", "/DeviceN", "/Pattern",
             "/ICCBased", "/Lab", "/CalRGB", "/CalGray", *INLINE_SPACES}


def _families(cs) -> list[str]:
    """Every colour space family a colour space uses: itself, a Separation/DeviceN alternate, an Indexed base."""
    if isinstance(cs, pikepdf.Array) and len(cs):
        fam = INLINE_SPACES.get(str(cs[0]), str(cs[0]))
        if fam in ("/Separation", "/DeviceN") and len(cs) > 2:
            return [fam, *_families(cs[2])]
        if fam == "/Indexed" and len(cs) > 1:
            return [fam, *_families(cs[1])]
        return [fam]
    return [INLINE_SPACES.get(str(cs), str(cs))] if cs is not None else []


def _bad_space(families: list[str], std: str, intent_n: int) -> str | None:
    """Why a colour space is not allowed on this standard, or None.

    `intent_n` is the number of components of the output intent profile (0 without one, which is reported on
    its own as NO_OUTPUT_INTENT). PDF/X-4 allows a device colour space only when it matches the output intent:
    DeviceRGB needs an RGB intent and DeviceCMYK a CMYK one (DeviceGray is allowed with any intent)."""
    if std == "pdf/x-1a":
        bad = [f for f in families if f not in X1A_SPACES]
        return f"{bad[0]} (PDF/X-1a allows CMYK, Gray, Separation and DeviceN only)" if bad else None
    if "/DeviceRGB" in families and intent_n not in (0, 3):
        return "/DeviceRGB (PDF/X-4 without an RGB output intent needs RGB to be ICC-tagged)"
    if "/DeviceCMYK" in families and intent_n not in (0, 4):
        return "/DeviceCMYK (PDF/X-4 without a CMYK output intent needs CMYK to be ICC-tagged)"
    return None


def _intent_components(pdf) -> int:
    oi = pdf.Root.get("/OutputIntents")
    if not oi:
        return 0
    try:
        return int(oi[0].get("/DestOutputProfile", {}).get("/N", 0))
    except (TypeError, ValueError, AttributeError):
        return 0


def check_content(pdf, std: str) -> list[Issue]:
    """Check every page and everything it reaches: resources (colour spaces, shadings, ExtGStates, fonts,
    images), nested Form XObjects and tiling patterns, annotation appearance streams, Type 3 glyph procedures,
    and the colour operators and inline images in every content stream.

    Each indirect object is checked once, so shared resources and self-referencing ones cannot repeat or loop. A
    bad colour space or shading resource is reported once, on the first page that uses it, with the number of
    pages. WeasyPrint's own crop marks were a Form with a transparency group, an ExtGState with `ca 0` and
    DeviceRGB strokes, all invisible to a check that only looked at the page's own resources."""
    return _Walker(std, _intent_components(pdf)).run(pdf)


class _Walker:
    def __init__(self, std: str, intent_n: int):
        self.std = std
        self.intent_n = intent_n
        self.tlevel = "error" if std == "pdf/x-1a" else "warning"
        self.seen: set = set()
        self.issues: list[Issue] = []
        self.shared: dict = {}          # (message, resource identity) -> pages it was found on
        self.n = 0

    def run(self, pdf) -> list[Issue]:
        for self.n, page in enumerate(pdf.pages, 1):
            group = page.obj.get("/Group")
            if group is not None and str(group.get("/S")) == "/Transparency":
                self.add("TRANSPARENCY", "page has a transparency group", level=self.tlevel)
            try:
                res = page.Resources
            except (AttributeError, KeyError):
                res = pikepdf.Dictionary()
            self.resources(res, "")
            self.operators(page.obj, "", res)
            for annot in page.obj.get("/Annots") or []:
                self.annotation(annot)
        for (msg, _), pages in self.shared.items():
            more = f" (on {len(pages)} pages, first {pages[0]})" if len(pages) > 1 else ""
            self.issues.append(Issue("VECTOR_COLORSPACE", msg + more, page=pages[0]))
        return self.issues

    def add(self, code: str, message: str, level: str = ""):
        self.issues.append(Issue(code, message, level=level, page=self.n))

    def once(self, obj) -> bool:
        """True the first time an indirect object is seen; direct objects are always checked."""
        if not isinstance(obj, pikepdf.Object) or not obj.is_indirect:
            return True
        if obj.objgen in self.seen:
            return False
        self.seen.add(obj.objgen)
        return True

    def space(self, cs, what: str):
        """A colour space or shading resource: reported once per resource, with the pages it is on."""
        why = _bad_space(_families(cs), self.std, self.intent_n)
        if why:
            key = cs.objgen if isinstance(cs, pikepdf.Object) and cs.is_indirect else repr(cs)
            pages = self.shared.setdefault((f"{what} is {why}", key), [])
            if self.n not in pages:
                pages.append(self.n)

    def resources(self, res, where: str):
        if not isinstance(res, pikepdf.Dictionary):
            return
        for k, g in (res.get("/ExtGState") or {}).items():
            if not self.once(g):
                continue
            smask = g.get("/SMask")
            if (float(g.get("/CA", 1)) < 1 or float(g.get("/ca", 1)) < 1
                    or str(g.get("/BM", "/Normal")) not in ("/Normal", "/Compatible")
                    or (smask is not None and str(smask) != "/None")):
                self.add("TRANSPARENCY", f"transparency in ExtGState {k}{where}", level=self.tlevel)
        for k, cs in (res.get("/ColorSpace") or {}).items():
            self.space(cs, f"colour space {k}{where}")
        for k, sh in (res.get("/Shading") or {}).items():
            self.space(sh.get("/ColorSpace"), f"shading {k}{where}")
        for k, fo in (res.get("/Font") or {}).items():
            if self.once(fo):
                self.font(k, fo, res, where)
        for name, x in (res.get("/XObject") or {}).items():
            if not self.once(x):
                continue
            sub = str(x.get("/Subtype"))
            if sub == "/Image":
                self.image(f"image {name}{where}", x.get("/ColorSpace"), bool(x.get("/ImageMask", False)),
                           "/SMask" in x)
            elif sub == "/Form":
                self.form(x, f" in form XObject {name}")
        for name, pat in (res.get("/Pattern") or {}).items():
            if not self.once(pat):
                continue
            if int(pat.get("/PatternType", 0)) == 1:
                self.form(pat, f" in pattern {name}")
            elif "/Shading" in pat:
                self.space(pat.Shading.get("/ColorSpace"), f"shading in pattern {name}")

    def font(self, name, fo, parent_res, where: str):
        if str(fo.get("/Subtype")) == "/Type3":
            # glyphs are content streams in the PDF itself: nothing to embed, but their operators count
            fres = fo.get("/Resources")
            res = fres if fres is not None else parent_res
            if fres is not None:
                self.resources(fres, f" in Type 3 font {name}{where}")
            for glyph, proc in (fo.get("/CharProcs") or {}).items():
                if self.once(proc):
                    self.operators(proc, f" in glyph {glyph} of Type 3 font {name}{where}", res)
            return
        fd = fo.get("/DescendantFonts")
        d = (fd[0] if fd else fo).get("/FontDescriptor")
        if not d or not any(k in d for k in ("/FontFile", "/FontFile2", "/FontFile3")):
            self.add("FONT_NOT_EMBEDDED", f"font {fo.get('/BaseFont')} not embedded")

    def image(self, what: str, cs, mask: bool, smask: bool):
        fam = _families(cs)
        if self.std == "pdf/x-1a":
            bad = [f for f in fam if f not in X1A_SPACES]
            if not mask and (not fam or bad):
                self.add("IMAGE_COLORSPACE", f"{what} is {bad[0] if bad else 'without a colour space'}; "
                         "PDF/X-1a allows CMYK/Gray only")
        elif fam[:1] == ["/DeviceRGB"]:
            self.add("IMAGE_COLORSPACE", f"{what} is untagged DeviceRGB; PDF/X-4 needs ICCBased RGB")
        elif not mask and (why := _bad_space(fam, self.std, self.intent_n)):
            self.add("IMAGE_COLORSPACE", f"{what} is {why}")
        if smask and self.std == "pdf/x-1a":
            self.add("IMAGE_SMASK", f"{what} has a soft mask (transparency)")

    def form(self, x, where: str):
        group = x.get("/Group")
        if group is not None and str(group.get("/S")) == "/Transparency":
            self.add("TRANSPARENCY", f"transparency group{where}", level=self.tlevel)
        res = x.get("/Resources")
        if res is not None:
            self.resources(res, where)
        self.operators(x, where, res)

    def annotation(self, annot):
        """Appearance streams (/AP /N, /R, /D, each a form or a dict of forms per appearance state) are
        printed like page content when the annotation is."""
        if not isinstance(annot, pikepdf.Dictionary) or not self.once(annot):
            return
        sub = str(annot.get("/Subtype", "")).lstrip("/")
        for kind, ap in (annot.get("/AP") or {}).items():
            forms = [ap] if isinstance(ap, pikepdf.Stream) else list((ap or {}).values())
            for f in forms:
                if isinstance(f, pikepdf.Stream) and self.once(f):
                    self.form(f, f" in the {kind.lstrip('/')} appearance of a {sub} annotation")

    def operators(self, obj, where: str, res):
        """DeviceRGB/DeviceCMYK set directly by colour operators (rg/RG, k/K, or cs/CS with a device space)
        and the colour space of inline images in a content stream."""
        try:
            ops = pikepdf.parse_content_stream(obj)
        except pikepdf.PdfError:
            return
        found: dict[str, int] = {}
        inline = 0
        for operands, op in ops:
            op = str(op)
            if op in ("rg", "RG"):
                fam = "/DeviceRGB"
            elif op in ("k", "K"):
                fam = "/DeviceCMYK"
            elif op in ("cs", "CS") and operands and str(operands[0]) in ("/DeviceRGB", "/DeviceCMYK"):
                fam = str(operands[0])
            elif op == "INLINE IMAGE" and operands:
                inline += 1
                self.inline_image(operands[0], inline, where, res)
                continue
            else:
                continue
            found[fam] = found.get(fam, 0) + 1
        for fam, count in found.items():
            why = _bad_space([fam], self.std, self.intent_n)
            if why:
                self.add("VECTOR_COLORSPACE", f"{count} {fam.lstrip('/')} colour operator(s) "
                         f"{where.strip() or 'in the page content'}: {why}")

    def inline_image(self, im, i: int, where: str, res):
        d = im.obj
        cs = d.get("/ColorSpace", d.get("/CS"))
        mask = bool(d.get("/ImageMask", d.get("/IM", False)))
        if cs is not None and not isinstance(cs, pikepdf.Array) and str(cs) not in _FAMILIES:
            named = ((res or {}).get("/ColorSpace") or {}).get(str(cs)) if isinstance(res, pikepdf.Dictionary) else None
            cs = named if named is not None else cs           # a resource name; unresolvable ones stay as is
        self.image(f"inline image {i} {where.strip() or 'in the page content'}", cs, mask, False)
