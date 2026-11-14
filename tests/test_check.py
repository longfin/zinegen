"""check.check_content and render.prune_color_spaces on small hand-built PDFs (no WeasyPrint needed)."""
from __future__ import annotations

import pikepdf
import pytest

from zinegen import check, render


def new_pdf(intent_n: int | None = 4, pages: int = 1) -> pikepdf.Pdf:
    """A PDF with `pages` empty pages and an output intent whose profile has `intent_n` components."""
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page()
    if intent_n:
        prof = pdf.make_stream(b"not a real profile")
        prof["/N"] = intent_n
        pdf.Root.OutputIntents = pikepdf.Array([pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.OutputIntent, S=pikepdf.Name.GTS_PDFX, OutputConditionIdentifier="test",
            DestOutputProfile=prof))])
    return pdf


def page_with(pdf: pikepdf.Pdf, n: int, data: bytes, **resources) -> pikepdf.Page:
    page = pdf.pages[n]
    page.obj.Contents = pdf.make_stream(data)
    page.obj.Resources = pikepdf.Dictionary(**resources)
    return page


def form(pdf: pikepdf.Pdf, data: bytes, resources: pikepdf.Dictionary | None = None) -> pikepdf.Object:
    f = pdf.make_stream(data)
    f.Type, f.Subtype, f.BBox = pikepdf.Name.XObject, pikepdf.Name.Form, [0, 0, 10, 10]
    if resources is not None:
        f.Resources = resources
    return pdf.make_indirect(f)


def codes(issues) -> list[tuple[str, str]]:
    return [(i.code, i.level) for i in issues]


# --- inline images -------------------------------------------------------------------------------------------

INLINE = b"q 10 0 0 10 0 0 cm BI /W 1 /H 1 %s /BPC 8 ID %s EI Q"


@pytest.mark.parametrize("cs, data, bad", [
    (b"/CS /RGB", b"\xff\x00\x00", True),
    (b"/ColorSpace /DeviceRGB", b"\xff\x00\x00", True),
    (b"/CS [/I /RGB 0 <ff0000>]", b"\x00", True),
    (b"/CS /Cs1", b"\xff\x00\x00", True),               # a resource name for DeviceRGB
    (b"/CS /G", b"\x80", False),
    (b"/CS /CMYK", b"\x00\x00\x00\xff", False),
    (b"/CS [/I /CMYK 0 <000000ff>]", b"\x00", False),
    (b"/IM true /BPC 1 /D [1 0]", b"\x80", False),        # a stencil mask has no colour space
])
def test_inline_image_on_x1a(cs: bytes, data: bytes, bad: bool):
    pdf = new_pdf(4)
    body = INLINE % (cs, data)
    if b"/IM" in cs:
        body = body.replace(b" /BPC 8", b"")
    named = b"/Cs1" in cs          # the resource itself is reported too (VECTOR_COLORSPACE); only add it here
    page_with(pdf, 0, body, **({"ColorSpace": pikepdf.Dictionary(Cs1=pikepdf.Name.DeviceRGB)} if named else {}))
    issues = [i for i in check.check_content(pdf, "pdf/x-1a") if not (named and i.code == "VECTOR_COLORSPACE")]
    if bad:
        assert codes(issues) == [("IMAGE_COLORSPACE", "error")], issues
        assert "inline image 1 in the page content is /DeviceRGB" in issues[0].message
    else:
        assert issues == []


def test_inline_rgb_image_in_form_on_x4_cmyk():
    pdf = new_pdf(4)
    f = form(pdf, INLINE % (b"/CS /RGB", b"\xff\x00\x00"), pikepdf.Dictionary())
    page_with(pdf, 0, b"/F1 Do", XObject=pikepdf.Dictionary(F1=f))
    issues = check.check_content(pdf, "pdf/x-4")
    assert codes(issues) == [("IMAGE_COLORSPACE", "error")]
    assert "in form XObject /F1" in issues[0].message and "untagged DeviceRGB" in issues[0].message


# --- annotations and Type 3 fonts ----------------------------------------------------------------------------

def test_annotation_appearance_streams_are_walked():
    pdf = new_pdf(4)
    page_with(pdf, 0, b"")
    normal = form(pdf, b"1 0 0 rg 0 0 5 5 re f", pikepdf.Dictionary())
    on = form(pdf, b"0 0 1 RG 0 0 m 5 5 l S", pikepdf.Dictionary())
    annot = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Square, Rect=[0, 0, 10, 10],
        AP=pikepdf.Dictionary(N=normal, D=pikepdf.Dictionary(On=on, Off=normal))))
    pdf.pages[0].obj.Annots = pikepdf.Array([annot])
    issues = check.check_content(pdf, "pdf/x-1a")
    assert codes(issues) == [("VECTOR_COLORSPACE", "error")] * 2       # each form once, though N and Off share
    assert {i.message.split(" colour operator")[0] for i in issues} == {"1 DeviceRGB"}
    assert all("appearance of a Square annotation" in i.message for i in issues)


def type3_font(pdf: pikepdf.Pdf, glyph: bytes, resources=None) -> pikepdf.Object:
    font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type3, FontBBox=[0, 0, 10, 10],
        FontMatrix=[0.1, 0, 0, 0.1, 0, 0], FirstChar=65, LastChar=65, Widths=[10],
        Encoding=pikepdf.Dictionary(Differences=[65, pikepdf.Name.a]),
        CharProcs=pikepdf.Dictionary(a=pdf.make_stream(glyph)))
    if resources is not None:
        font.Resources = resources
    return pdf.make_indirect(font)


def test_type3_font_is_walked_not_reported_unembedded():
    pdf = new_pdf(4)
    font = type3_font(pdf, b"10 0 d0 0 1 0 rg 0 0 10 10 re f")
    page_with(pdf, 0, b"BT /T3 12 Tf (A) Tj ET", Font=pikepdf.Dictionary(T3=font))
    issues = check.check_content(pdf, "pdf/x-1a")
    assert codes(issues) == [("VECTOR_COLORSPACE", "error")]
    assert "glyph /a of Type 3 font /T3" in issues[0].message
    # a clean Type 3 font raises nothing (it used to be FONT_NOT_EMBEDDED)
    pdf2 = new_pdf(4)
    page_with(pdf2, 0, b"BT /T3 12 Tf (A) Tj ET", Font=pikepdf.Dictionary(T3=type3_font(pdf2, b"10 0 d0 0 0 10 10 re f")))
    assert check.check_content(pdf2, "pdf/x-1a") == []


def test_unembedded_truetype_is_still_reported():
    pdf = new_pdf(4)
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.TrueType,
                                                BaseFont=pikepdf.Name.Arial))
    page_with(pdf, 0, b"BT /F1 12 Tf (A) Tj ET", Font=pikepdf.Dictionary(F1=font))
    assert codes(check.check_content(pdf, "pdf/x-4")) == [("FONT_NOT_EMBEDDED", "error")]


# --- device colour against the output intent -----------------------------------------------------------------

@pytest.mark.parametrize("intent_n, op, bad", [
    (3, b"1 0 0 rg", False), (4, b"1 0 0 rg", True), (1, b"1 0 0 rg", True),
    (4, b"0 0 0 1 k", False), (3, b"0 0 0 1 k", True), (1, b"0 0 0 1 K", True),
    (3, b"/DeviceCMYK cs 0 0 0 1 sc", True), (1, b"/DeviceRGB CS 0 0 0 SC", True),
    (1, b"0.5 g", False), (3, b"0.5 g", False), (4, b"0.5 G", False),
    (None, b"1 0 0 rg 0 0 0 1 k", False),       # no output intent: NO_OUTPUT_INTENT reports that instead
])
def test_device_colour_must_match_x4_intent(intent_n, op: bytes, bad: bool):
    pdf = new_pdf(intent_n)
    page_with(pdf, 0, op + b" 0 0 5 5 re f")
    issues = check.check_content(pdf, "pdf/x-4")
    assert codes(issues) == ([("VECTOR_COLORSPACE", "error")] if bad else []), issues


def test_device_cmyk_resource_on_rgb_intent():
    pdf = new_pdf(3)
    sep = pikepdf.Array([pikepdf.Name.Separation, pikepdf.Name("/All"), pikepdf.Name.DeviceCMYK,
                         pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[1, 1, 1, 1], N=1)])
    page_with(pdf, 0, b"/R cs 1 scn 0 0 5 5 re f", ColorSpace=pikepdf.Dictionary(R=sep))
    issues = check.check_content(pdf, "pdf/x-4")
    assert codes(issues) == [("VECTOR_COLORSPACE", "error")] and "/DeviceCMYK" in issues[0].message


# --- de-duplication and loops --------------------------------------------------------------------------------

def test_shared_bad_colour_space_reported_once():
    pdf = new_pdf(4, pages=3)
    prof = pdf.make_stream(b"rgb profile")
    prof["/N"] = 3
    icc = pdf.make_indirect(pikepdf.Array([pikepdf.Name.ICCBased, prof]))
    shading = pdf.make_indirect(pikepdf.Dictionary(ShadingType=2, ColorSpace=pikepdf.Name.DeviceRGB,
                                                   Coords=[0, 0, 1, 0], Function=pikepdf.Dictionary(
                                                       FunctionType=2, Domain=[0, 1], C0=[0, 0, 0], C1=[1, 1, 1], N=1)))
    for n in range(3):
        page_with(pdf, n, b"/P cs", ColorSpace=pikepdf.Dictionary(P=icc), Shading=pikepdf.Dictionary(Sh=shading))
    issues = check.check_content(pdf, "pdf/x-1a")
    assert codes(issues) == [("VECTOR_COLORSPACE", "error")] * 2
    assert all(i.page == 1 and "(on 3 pages, first 1)" in i.message for i in issues)
    assert {i.message.split(" is ")[0] for i in issues} == {"colour space /P", "shading /Sh"}


def test_self_referencing_resources_terminate():
    pdf = new_pdf(4)
    res = pdf.make_indirect(pikepdf.Dictionary())
    f = form(pdf, b"1 0 0 rg /F1 Do", res)
    res.XObject = pikepdf.Dictionary(F1=f)                   # the form draws itself
    font = type3_font(pdf, b"10 0 d0 /F1 Do", res)
    res.Font = pikepdf.Dictionary(T3=font)                   # the font's resources hold the font
    page = page_with(pdf, 0, b"/F1 Do")
    page.obj.Resources = res
    issues = check.check_content(pdf, "pdf/x-1a")
    assert codes(issues) == [("VECTOR_COLORSPACE", "error")]
    render.prune_color_spaces(pdf, strict=True)              # must terminate too


# --- render.prune_color_spaces -------------------------------------------------------------------------------

def _spaces(pdf):
    prof = pdf.make_stream(b"profile")
    prof["/N"] = 3
    lab = pikepdf.Array([pikepdf.Name.Lab, pikepdf.Dictionary(WhitePoint=[0.9505, 1, 1.089])])
    return pikepdf.Array([pikepdf.Name.ICCBased, prof]), lab


@pytest.mark.parametrize("strict", [True, False])
def test_prune_reaches_forms(strict: bool):
    pdf = new_pdf(4)
    icc, lab = _spaces(pdf)
    inner = form(pdf, b"/used cs 0 0 0 sc BI /W 1 /H 1 /CS /img /BPC 8 ID \x00\x00\x00 EI",
                 pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(**{"used": icc, "img": icc, "unused": icc,
                                                                     "lab-d50": lab})))
    bare = form(pdf, b"/pageused cs 0 0 0 sc")              # no /Resources: uses the page's
    page_with(pdf, 0, b"/Inner Do /Bare Do",
              ColorSpace=pikepdf.Dictionary(**{"pageused": icc, "pageunused": icc, "lab-d65": lab}),
              XObject=pikepdf.Dictionary(Inner=inner, Bare=bare))
    render.prune_color_spaces(pdf, strict)
    page_cs = set(pdf.pages[0].Resources.ColorSpace.keys())
    form_cs = set(inner.Resources.ColorSpace.keys())
    if strict:
        assert page_cs == {"/pageused"}
        assert form_cs == {"/used", "/img"}
    else:
        assert page_cs == {"/pageused", "/pageunused"}
        assert form_cs == {"/used", "/img", "/unused"}


def test_prune_keeps_names_of_every_user_of_shared_resources():
    pdf = new_pdf(4, pages=2)
    icc, _ = _spaces(pdf)
    res = pdf.make_indirect(pikepdf.Dictionary(ColorSpace=pikepdf.Dictionary(a=icc, b=icc, c=icc)))
    for n, data in enumerate((b"/a cs", b"/b CS")):
        page_with(pdf, n, data).obj.Resources = res
    render.prune_color_spaces(pdf, strict=True)
    assert set(res.ColorSpace.keys()) == {"/a", "/b"}
