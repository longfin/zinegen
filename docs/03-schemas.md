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
| `page_numbers` | bool | false | not on title/blank/full-bleed pages |
| `marks` | bool | true | crop + registration marks |
| `output.standard` | `pdf/x-1a` `pdf/x-4` | pdf/x-1a | |
| `output.icc` | path or profile name, or null | null | required for x-1a; also the output intent for x-4 |
| `output.intent` | perceptual / relative-colorimetric / saturation / absolute-colorimetric | perceptual | |
| `output.jpeg_quality` | int 50–100 | 95 | CMYK conversion only; x-4 embeds source bytes |
| `source.album_id` | 32 hex | – | |
| `source.size` | 640 / 1280 / 2048 / 2560 / fullsize | fullsize | |
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

Written by `zinegen init`; read by agents when sequencing. Mirrors the fields proposed for lr-mcp (issue #10).

```json
{"version": 1,
 "album": {"id": "<32hex>", "name": "…", "sort": "captureDateAsc | captureDateDesc | customAsc"},
 "photos": [{"id": "<32hex>", "file": "R0010712.DNG", "captured": "2026-11-07T14:08:16",
             "order": "V--3 | null", "title": null, "caption": null, "keywords": [],
             "rating": null, "flag": "pick | reject | null", "camera": "…", "lens": "…",
             "original": [6192, 4128], "cropped": [6192, 4128], "orientation": 1,
             "changed_at": "2026-11-07T09:43:59.021Z"}]}
```

`photos` is in the album's own order. `cropped` is the post-crop size reported by Lightroom; the embedded
rendition's pixel size is authoritative for layout. `keywords` is a list of keyword strings; 0.1.0 wrote
`[true, …]` instead (the API sends `dc.subject` as `{keyword: true}`). This is a bug fix, not a format change.
To refresh a 0.1.0 `photos.json` without touching `book.yaml`, run `zinegen init <album> --dir <scratch dir>`
and copy its `photos.json` over (`--force` in the book directory would overwrite `book.yaml`).

## 3. `out/report.json` — render report (version 1)

```json
{"version": 1, "title": "…", "pages": 28, "trim_mm": [148, 210], "bleed_mm": 3, "binding": "perfect",
 "standard": "pdf/x-1a", "icc": "/path/JapanColor2001Coated.icc", "icc_description": "Japan Color 2001 Coated",
 "pdf": "…/out/interior.pdf", "html": "…/out/interior.html",
 "images": [{"page": 3, "photo": "<32hex>", "template": "full-bleed", "placed_mm": [324.0, 216.0],
             "px": [6192, 4128], "dpi": 485, "level": "ok | low | bad", "crossing_gutter": false}],
 "issues": [ …issue… ]}
```

`placed_mm` is the full scaled image size (a cover placement is larger than the box it fills), so
`dpi = px[0] / (placed_mm[0] / 25.4)`. `level`: ok ≥ 300, low ≥ 200, bad < 200. Spread images appear once per page; their issues (dpi, gutter) are raised once, on the verso page.

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
| `NO_ROOM` | error | layout (margins, `gap_mm` or caption leave a photo box under 5 mm; photos not placed) |
| `NO_OUTPUT_INTENT`, `NO_DEST_PROFILE`, `NO_PDFX_VERSION`, `PDF_VERSION` | error | check |
| `NO_TRIMBOX`, `TRIMBOX_SIZE`, `BLEED_SMALL` | error | check |
| `NO_BLEEDBOX` | warning | check |
| `IMAGE_COLORSPACE`, `IMAGE_SMASK`, `FONT_NOT_EMBEDDED` | error | check |
| `TRANSPARENCY` | error on x-1a, warning on x-4 | check |

## Not frozen (yet)

- `cache/index.json` (rendition cache sidecar): internal until it is shared with lr-mcp (issue #11).
- `cache/cmyk/*.json`: conversion sidecar keyed by content hash.
- Proof file naming `proof/spread-NN.png` / `page-NNN.png`, a layout export and a comments file: to be fixed
  together when the review GUI is started.

## Migration notes

- v1: initial. Pre-version specs need `version: 1` added and `_file` hints renamed to `note`.
