# Frozen data formats

Four files are contracts between the agent, the user, the renderer and (later) the review GUI. They carry a
`version` and are documented here. Adding a field is always allowed; renaming, removing or changing the meaning
of a field bumps the version and gets a migration note below. Everything else under `books/<name>/`
(`cache/`, `out/interior.html`, `proof/*.png`) is derived and may change without notice.

## 1. `book.yaml` — the book spec (version 1)

Schema: `src/zinegen/schema/book.schema.json` (JSON Schema 2020-12), enforced by `spec.load`.
Unknown keys are errors. Units are in the key name: `_mm`, `_um`, `_pt`.

| Key | Type | Default | Notes |
|---|---|---|---|
| `version` | `1` | required | |
| `title` | string | directory name | |
| `trim` | `"A5"` / `{preset}` / `{width_mm, height_mm}` | A5 | presets: `zinegen presets` |
| `bleed_mm` | number 0–10 | 3 | |
| `binding` | `saddle` `perfect` `hardcover` `layflat` | perfect | page multiple 4/2/16/2; gutter allowance 0/4/3/0 mm |
| `paper.caliper_um`, `paper.name` | number, string | – | spine/cover only; interior renderer ignores it |
| `margins_mm.{top,bottom,inside,outside}` | number | 14/16/16/12 | inside/outside are mirrored per page |
| `fonts.{body,caption,title}` | fontconfig family | Nanum Myeongjo / Nanum Gothic / Nanum Myeongjo | |
| `type.{body_pt,caption_pt,title_pt}` | number | 10 / 7.5 / 24 | |
| `page_numbers` | bool | false | folio at the bottom outside corner of every page except `title`, `blank` (including inserted/padding blanks), `full-bleed`, and both pages of `spread-full`/`spread-margin` |
| `marks` | bool | true | crop marks at the trim corners and registration targets at the middle of each edge, drawn outside the bleed (they start 1 mm beyond the BleedBox) in the registration colour (Separation `/All`), with the MediaBox enlarged to hold them; TrimBox and BleedBox are unchanged. `false`: MediaBox = BleedBox, no marks |
| `output.standard` | `pdf/x-1a` `pdf/x-4` | pdf/x-1a | |
| `output.icc` | path or profile name, or null | null | required for x-1a; also the output intent for x-4. A value containing `/` or `\`, starting with `~` or ending in `.icc`/`.icm` is a file path; a relative one is resolved against the book directory (where book.yaml is), never the working directory. Any other value is a profile name, searched only in the profile directories (`color.ADOBE_PROFILE_DIRS`) as `<name>`, `<name>.icc`, `<name>.icm` (a path that is not an existing file is looked up there too). Set but unresolvable: render fails |
| `output.intent` | perceptual / relative-colorimetric / saturation / absolute-colorimetric | perceptual | |
| `output.jpeg_quality` | int 50–100 | 95 | the x-1a CMYK conversion, and the upright copy of an EXIF-rotated photo on x-4 (`ORIENTATION_REENCODED`); x-4 otherwise embeds source bytes |
| `output.max_dpi` | int 150–2400, or null | 350 | x-1a only: each photo is resampled during its single CMYK conversion so its most demanding placement gets this effective resolution (never upsampled); a photo at most 1.1× above it (`render.RESAMPLE_TOLERANCE`) is converted at its source size. null keeps every source pixel. Below 300 every downsampled image is reported `LOW_DPI` (below 200 `BAD_DPI`). x-4 embeds source bytes |
| `source.album_id` | 32 hex | – | |
| `source.size` | string: `"640"` / `"1280"` / `"2048"` / `"2560"` / `fullsize` | fullsize | quote the number in YAML (`size: "2560"`); a bare `2560` is an integer and fails validation |
| `pages[]` | list of page items | required | see below |

Page items. `photo` is a 32-hex Lightroom asset id; `note` (string) is allowed on every item and ignored.

| `template` | pages | required | optional |
|---|---|---|---|
| `title` | 1 | `title` | `subtitle` |
| `blank` | 1 | | |
| `text` | 1 | `text` | `heading` |
| `colophon` | 1 | `text` | |
| `full-bleed` | 1 | `photo` | `focal: [x, y]` 0..1 |
| `margin` | 1 | `photo` | `caption`, `valign` top/center/bottom, `caption_align` left/center/right |
| `pair` | 1 | `photos: [a, b]` | `direction` auto/row/column, `gap_mm`, `caption` |
| `spread-full` | 2 | `photo` | `focal` |
| `spread-margin` | 2 | `photo` | `caption` |

Trim preset keys and their sizes are part of this format: a key is never renamed and its size never changes;
new sizes get new keys. Labels may be corrected. `CROWN` stays 152×225 mm (신국판, mislabelled 크라운판 in 0.1.0);
use `SINGUK` for 신국판 and `CROWN176` for 크라운판 (176×248). Preset names are case-insensitive.

Layout rules applied after validation: page 1 is a recto; a 2-page template starting on a recto gets a blank
inserted before it (`SPREAD_MOVED`); the total is padded with blanks to the binding multiple (`PAGES_PADDED`).

## 2. `photos.json` — album metadata (version 1)

Written by `zinegen init`; read by agents when sequencing.

```json
{"version": 1,
 "album": {"id": "<32hex>", "name": "…", "sort": "captureDateAsc | captureDateDesc | customAsc"},
 "photos": [{"id": "<32hex>", "file": "R0010712.DNG", "captured": "2026-11-07T14:08:16",
             "order": "V--3 | null", "title": null, "caption": null, "keywords": [],
             "rating": null, "flag": "pick | reject | null", "camera": "…", "lens": "…",
             "original": [6192, 4128], "cropped": [6192, 4128], "orientation": 1,
             "changed_at": "2026-11-07T09:43:59.021Z", "edited_at": "2026-11-07T09:40:12.345Z"}]}
```

`changed_at` is Lightroom's `changedAtTime` (else `userUpdated`): it moves on any change to the photo,
a rating or flag included. `edited_at` (added after 0.2.0) is the time of the last develop edit, crop included:
`develop.userUpdated`, else `userUpdated`, else `changedAtTime`, as lr-mcp's download cache uses. `zinegen fetch`
skips a photo whose cached rendition has the same size and `edited_at` (a photo with no edit time at all is
always downloaded); a size change, a missing file or `--force` downloads it again. Older files lack `edited_at`; readers treat it as absent.

`photos` is in the album's own order. `cropped` is the post-crop size reported by Lightroom; the embedded
rendition's pixel size is authoritative for layout. `keywords` is a list of keyword strings; 0.1.0 wrote
`[true, …]` instead (the API sends `dc.subject` as `{keyword: true}`). This is a bug fix, not a format change.
To refresh a 0.1.0 `photos.json` without touching `book.yaml`, run `zinegen init --album <album id> --dir <scratch dir>`
and copy its `photos.json` over (`--force` in the book directory would overwrite `book.yaml`).

## 3. `out/report.json` — render report (version 1)

```json
{"version": 1, "title": "…", "pages": 28, "trim_mm": [148, 210], "bleed_mm": 3, "binding": "perfect",
 "standard": "pdf/x-1a", "icc": "/path/JapanColor2001Coated.icc", "icc_description": "Japan Color 2001 Coated",
 "max_dpi": 350,
 "pdf": "…/out/interior.pdf", "html": "…/out/interior.html",
 "images": [{"page": 3, "photo": "<32hex>", "template": "full-bleed", "placed_mm": [324.0, 216.0],
             "px": [4465, 2977], "source_px": [6192, 4128], "dpi": 350, "level": "ok | low | bad", "crossing_gutter": false}],
 "issues": [ …issue… ]}
```

`placed_mm` is the full scaled image size (a cover placement is larger than the box it fills), so
`dpi = px[0] / (placed_mm[0] / 25.4)`. `level`: ok ≥ 300, low ≥ 200, bad < 200. Spread images appear once per page; their issues (dpi, gutter) are raised once, on the verso page.
`px` is the pixel size of the image actually embedded in the PDF and `source_px` that of the cached rendition;
they differ only on x-1a when `max_dpi` downsampled the photo. Layout geometry always comes from `source_px`.
`max_dpi` is the setting applied to the embedded images (null on x-4 or when disabled).

## 4. `out/check.json` — PDF/X self-check (version 1)

```json
{"version": 1, "ok": true, "standard": "pdf/x-1a",
 "info": {"pdf_version": "1.4", "pages": 28, "output_intent": "Japan Color 2001 Coated", "pdfx_version": "PDF/X-1a:2003"},
 "issues": [ …issue… ]}
```

`ok` is false when any issue has `level: error`. Issues from `report.json` are merged in.

## Issue object (shared by 3 and 4)

```json
{"code": "GUTTER_CROSSING", "level": "info | warning | error", "message": "…", "page": 9, "photo": "<32hex>"}
```

`page` and `photo` are omitted when not applicable. Codes and default levels live in `src/zinegen/issues.py`:

| Code | Level | Source |
|---|---|---|
| `SPREAD_MOVED`, `PAGES_PADDED` | info | spec |
| `LOW_DPI` | warning | layout |
| `BAD_DPI` | error | layout |
| `GUTTER_CROSSING` | warning | layout (perfect/hardcover only) |
| `NO_ROOM` | error | layout (margins, `gap_mm` or caption leave a photo box under 5 mm; photos not placed. Also: a text page's body has less than one line of height; the body is not printed) |
| `NO_OUTPUT_PROFILE` | error | render: x-4 with `output.icc` unset (a set but unresolvable profile fails the render instead) |
| `DPI_ABOVE_NEED` | info | render: x-4 placements above 1.5 × `output.max_dpi` (default 350); x-4 embeds source bytes unchanged. Suggests a smaller `source.size` only if every placement stays ≥ 300 dpi with it |
| `ORIENTATION_REENCODED` | warning | render: x-4 photo with an EXIF orientation tag; rotated and JPEG-encoded once (`output.jpeg_quality`) to embed it upright, so it is not the source bytes. x-1a applies the orientation during its single CMYK conversion (no issue) |
| `TEXT_CLIPPED` | warning | render: a text box (caption, body, colophon, title, ...) has more lines than fit; `page` is set. Boxes hold a whole number of lines (a caption two), so the cut falls between lines. Fixed-height boxes (title, subtitle, heading, colophon, folio) keep at least one line even when the type is taller |
| `DISPLAY_OUTPUT_PROFILE` | warning | render: `output.icc` is not a printer-class (`prtr`) ICC profile, e.g. the bundled `sRGB2014.icc` (display class). Valid PDF/X-4, but the output intent describes a screen |
| `NO_OUTPUT_INTENT`, `NO_DEST_PROFILE`, `NO_PDFX_VERSION`, `PDF_VERSION` | error | check |
| `NO_TRIMBOX`, `TRIMBOX_SIZE`, `BLEED_SMALL` | error | check (`BLEED_SMALL`: the BleedBox is less than `bleed_mm` beyond the TrimBox on any side) |
| `NO_BLEEDBOX` | warning | check |
| `IMAGE_COLORSPACE`, `IMAGE_SMASK`, `FONT_NOT_EMBEDDED` | error | check (images and fonts in nested Form XObjects, annotation appearance streams and Type 3 glyphs included; inline images `BI … EI` too, abbreviated `/CS /RGB` etc. resolved). A Type 3 font is not `FONT_NOT_EMBEDDED`: its glyphs are content streams, which are checked |
| `TRANSPARENCY` | error on x-1a, warning on x-4 | check: ExtGState alpha (`CA`/`ca` < 1), blend mode, soft mask, or a transparency group, on the page or in any Form XObject or tiling pattern it reaches |
| `VECTOR_COLORSPACE` | error | check: vector colour the standard does not allow, in the page or anything it reaches (nested Forms, patterns, annotation appearances, Type 3 glyphs): `rg`/`RG`/`cs /DeviceRGB` and colour space resources that are (or have an alternate/base that is) RGB, Lab or ICC-based on x-1a; on x-4 a device space that does not match the output intent: DeviceRGB without an RGB intent, DeviceCMYK (`k`/`K`, `cs /DeviceCMYK`, a CMYK alternate) without a CMYK intent. A colour space or shading resource shared by several pages is reported once, with `page` its first page and the page count in the message |

## Not frozen (yet)

- `cache/index.json` (rendition cache sidecar, `{asset id: {size, changed_at, edited_at, bytes, file, fetched_at}}`):
  internal. A rendition is current when `size` and `edited_at` match; an entry from 0.2.0 has no `edited_at`
  and stays current while `changed_at` matches, and then gets `edited_at` added.
- `cache/cmyk/*.json`: conversion sidecar keyed by content hash.
- `cache/upright/`: upright copies of EXIF-rotated photos for x-4.
- `cache/missing.json`: ids the last fetch did not find in Lightroom, so render can name them.
- Proof file naming `proof/spread-NN.png` / `page-NNN.png`, a layout export and a comments file: to be fixed
  together when the review GUI is started.

## Migration notes

- v1: initial. Pre-version specs need `version: 1` added and `_file` hints renamed to `note`.
- v1, added (no version bump): issue code `NO_OUTPUT_PROFILE`.
- v1, added (no version bump): issue code `NO_ROOM`. Clarified: dpi and gutter issues of a spread photo are
  raised once, on the verso page (0.1.0 raised them once per page).
- v1, added (no version bump): `book.yaml` `output.max_dpi`; `report.json` `max_dpi` and `images[].source_px`;
  issue code `DPI_ABOVE_NEED`. Existing x-1a books are now downsampled to 350 dpi by default; set
  `output.max_dpi: null` to keep every source pixel.
- v1, added (no version bump): issue code `ORIENTATION_REENCODED`. Clarified: a relative `output.icc` is
  resolved against the book directory (it used to depend on the working directory); `px`/`source_px`
  are sizes as displayed, with EXIF orientation applied.
- v1, added (no version bump): issue codes `TEXT_CLIPPED`, `DISPLAY_OUTPUT_PROFILE`, `VECTOR_COLORSPACE`.
  Corrected to match the schema text: `marks: true` now draws the marks outside the bleed in the
  registration colour and enlarges the MediaBox (0.1.0 used WeasyPrint's marks: DeviceRGB, a transparency
  group and inside the bleed, with MediaBox = BleedBox). TrimBox/BleedBox are unchanged. `check.json` now
  looks into nested Form XObjects, so a PDF written by 0.1.0 with marks fails the x-1a check.
- v1, clarified (no version bump): `BLEED_SMALL` looks at all four sides, and the BleedBox is now the real
  bleed for any `bleed_mm` (WeasyPrint capped it at 10 pt, so a bleed above 3.53 mm failed `check`).
  `VECTOR_COLORSPACE` also covers DeviceCMYK on x-4 without a CMYK output intent and DeviceRGB on x-4 with a
  Gray intent; a shared colour-space or shading resource is reported once (first page, page count in the
  message) instead of once per page. `check` also inspects inline images, annotation appearance streams and
  Type 3 glyphs, and no longer reports a Type 3 font as `FONT_NOT_EMBEDDED`. Text on x-4 with an RGB or Gray
  output intent is drawn as a lightness-matched neutral in the intent profile (ICCBased) instead of DeviceCMYK,
  so **x-4 PDFs rendered earlier with an RGB or Gray output intent now fail `check` with `VECTOR_COLORSPACE` and
  must be rendered again**. For a resource shared by several pages, a `VECTOR_COLORSPACE` issue's `page` is now
  the first page (the message gives the page count). `NO_ROOM` also covers a text page body with less than one
  line of height.
