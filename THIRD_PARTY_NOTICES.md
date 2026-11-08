# Third-party notices

## Bundled

**`src/zinegen/templates/sRGB2014.icc`** — Copyright International Color Consortium, 2015.
Obtained from the WeasyPrint distribution (BSD-3-Clause), which bundles the ICC's reference sRGB profile.
The ICC makes this profile available under its standard terms: it may be copied, distributed, embedded, made,
used and sold without restriction; altered versions must have the original identification and copyright
information removed and must not be misrepresented as the original profile. zinegen ships it unaltered and
uses it only as a fallback RGB tag for untagged images and as an RGB output intent in tests.

## Not bundled, resolved on the user's machine

- **CMYK press profiles** (Japan Color 2001 Coated, Coated FOGRA39, U.S. Web Coated (SWOP) and others) are
  looked up in Adobe/ColorSync/`/usr/share/color/icc` directories or taken from `output.icc`. They are never
  copied into this repository or the package; their own licenses apply. ECI profiles (eci.org) are a free
  alternative.
- **Fonts** are resolved through fontconfig and subset-embedded into the PDF. Default families (Nanum, Noto)
  are SIL Open Font License fonts that permit embedding. If you configure a proprietary font in `fonts:`,
  make sure its license allows embedding in documents.
- **Ghostscript** (AGPL-3.0) is invoked as a separate `gs` process for proof images only; it is not linked or
  redistributed. Anyone who bundles Ghostscript together with zinegen (containers, installers) takes on the
  AGPL obligations for that bundle.
- **Pango / Cairo / GLib** (LGPL-2.1+) are loaded by WeasyPrint as shared libraries.

## Python dependencies

| Package | License |
|---|---|
| lr-mcp | MIT |
| mcp | MIT |
| WeasyPrint, pydyf, tinycss2, Jinja2 | BSD-3-Clause |
| pikepdf | MPL-2.0 |
| Pillow (with LittleCMS) | MIT-CMU (LittleCMS: MIT) |
| jsonschema, PyYAML, fonttools | MIT |
