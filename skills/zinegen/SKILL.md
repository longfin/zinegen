---
name: zinegen
description: Typeset a photobook/zine from a Lightroom (cloud) album into a print-ready PDF/X with the zinegen CLI or MCP server. Use when asked to make a photobook, zine, 사진집, 포토북, 조판, or a print PDF from Lightroom photos.
license: MIT
compatibility: Requires the zinegen CLI on PATH (uv tool install zinegen) with pango and Ghostscript, and a Lightroom sign-in through lr-mcp
metadata:
  author: longfin
  version: "0.2.0"
---

# zinegen — agent-driven photobook typesetting

You are the typesetter. The user curates in Lightroom and reviews proofs; you write and iterate `book.yaml`.
Everything is derived from that one file. Never hand-edit the PDF or HTML.

## Tools

MCP (server name `zinegen`): `zg_spec_help`, `zg_list_albums`, `zg_album_photos`, `zg_init_book`, `zg_fetch`,
`zg_render`, `zg_proof` (returns spread images), `zg_check`, `zg_spine`.
CLI equivalents: `zinegen albums | init | fetch | render | proof | check | build | spine | presets | doctor`.
Lightroom browsing (ratings, filters, viewing candidates) comes from the `lightroom` MCP server (lr-mcp).

The `book` argument of `zg_fetch`, `zg_render`, `zg_proof` and `zg_check` takes the book directory or the
`book.yaml` path. Always pass **absolute paths** (`book`, and `dir` for `zg_init_book`): relative paths resolve
against the MCP server's working directory, which you cannot see when the plugin starts the server.

Expected failures come back as tool errors whose text says what to fix (bad `book.yaml`, a step run out of
order, an unknown album id or ICC profile). Read the message and fix the cause rather than retrying.

## Workflow

1. **Pick the album.** `zg_list_albums` with `query` (case-insensitive substring of the album name). Without a
   query it lists every album in the catalog, which can be very long, and it shows no photo counts.
   `zg_album_photos` takes the 32-hex album id (not a name) and shows the album's own order, crop size (L/P),
   rating, flag and the Lightroom title/caption of each photo.
2. **Print target.** Ask (or infer from the request) trim size, binding and print target. Offset/국내 인쇄소 →
   `pdf/x-1a` with a CMYK profile (`JapanColor2001Coated` is the Korean/Japanese default; `CoatedFOGRA39` for
   Europe; `USWebCoatedSWOP` for US). POD / Blurb-like services → `pdf/x-4` (photos embedded as the source
   JPEGs, tagged Display P3; the output intent still needs the printer's profile). An RGB output intent (the
   bundled sRGB) is for proofs only: black text is then RGB black, which a printer converts to four-colour black.
   For press, set a CMYK `output.icc` (text stays pure K).
3. **Draft.** `zg_init_book` with `album_id`, an absolute `dir`, `standard` (`pdf/x-1a` default, or `pdf/x-4`)
   and `icc` (profile name or path, see "Colour and file size"; default: the first of `JapanColor2001Coated`,
   `CoatedFOGRA39` found on the machine). It writes `book.yaml` (sequence = album order, templates
   auto-assigned) and `photos.json` (metadata) and returns the absolute `book.yaml` path. An unresolvable
   `icc`, or a non-CMYK one for `pdf/x-1a`, is rejected; an existing `book.yaml` is kept unless `force=true`.
   The draft is always A5, perfect binding, 3 mm bleed: set `trim`, `binding`, `bleed_mm` and `margins_mm` in
   the YAML. Read both files.
4. **Edit `book.yaml`**: sequence, template per page, `focal` for cropped placements, captions, title and
   colophon text. Captions: the draft copies the Lightroom *caption* into `margin` pages only. Lightroom
   *titles* are never copied, and photos drafted as `full-bleed`/`spread-full` (which take no caption) lose
   theirs. Look up `title`/`caption` in `photos.json` and carry them over by hand when you change templates.
   Use `note` on a page for remarks; the renderer ignores it. Write caption/essay drafts yourself; keep them
   short and mark them clearly so the user can edit the YAML.
   The file starts with `version: 1` and is schema-checked by every tool that reads it. A mistake returns a
   tool error `book.yaml is not a valid book spec:` with one line per problem and its path (e.g.
   `binding: 'spiral' is not one of [...]`, `pages[3] (margin): ...`); per-page problems are listed once the
   top-level ones are fixed. A YAML syntax error returns `book.yaml is not valid YAML: ...`.
5. **Fetch.** `zg_fetch` downloads the renditions into `cache/`. The first full-size download of 20 photos
   took 15–25 s in a test (it depends on the connection and the photos' size) and reports no progress until
   it returns, so tell the user before a large fetch. Later runs skip unchanged photos, and an interrupted fetch keeps what it
   finished. `source.size` (`fullsize` default, or `"2560"`, `"2048"`, `"1280"`, `"640"` px on the long edge)
   sets the rendition size; it is a string, so quote the number in YAML (`size: "2560"`; a bare `2560` fails
   validation): keep `fullsize` for offset; a smaller size downloads faster and is enough for small
   placements (check the dpi in the report). Changing it downloads everything again. The result lists
   `downloaded`, `skipped`, `errors`, `outside_album` (found by id outside the album) and `missing` (not in
   the catalog at all, with a `warning`): fix `missing` ids in `book.yaml` before rendering (render names
   them as not in Lightroom; fetching again does not help).
6. **Render and look.** `zg_render` → `zg_proof`. `zg_proof` returns at most 12 spreads per call; its first
   text block says how many spreads the book has and lists every spread not shown, with a `spreads=[...]` call
   for the next batch of up to 12. A call with explicit `spreads` does not repeat that list, so for a book over
   24 spreads keep track yourself and request the rest in batches of up to 12. Spread 1 is page 1; spread n is pages 2n-2 and 2n-1; each image is labelled with its pages.
   The default `dpi` 40 is enough for layout but too small to read captions or the colophon; use `dpi` 60–90
   for those, on selected `spreads`. Look at every spread. Critique like an editor: pacing (rhythm of
   full-bleed vs margin pages), facing-page relationships, horizon alignment, repeated motifs, dead white
   space, subjects cut by the gutter, captions competing with images. Fix in `book.yaml`, re-render. Two or
   three passes.
7. **Check.** `zg_check` must return `ok: true`. `zg_render` succeeds even when its `issues` contain errors (a
   `BAD_DPI` photo, a `NO_ROOM` page, `NO_OUTPUT_PROFILE`); `zg_check` merges those issues with its own PDF
   checks and is `ok: false` while any issue has `level: error`. Fix every error and report warnings to the
   user. Render itself fails (a tool error, no PDF written) for an invalid spec, a photo not fetched yet, or
   an `output.icc` that cannot be resolved.
8. **Deliver** `out/interior.pdf` and tell the user what to confirm with the printer: paper caliper for the
   spine (`zg_spine`, which rounds the page count up to the binding's multiple and computes the width for the
   rounded count), whether they accept PDF/X-1a or X-4, and the ICC profile used.

## book.yaml keys besides the pages

Use `zg_spec_help` for the full list. The ones a draft does not show:

- `page_numbers: true` adds a folio (in the caption font, at the bottom outside corner) to every page except
  `title`, `blank` (inserted and padding blanks included), `full-bleed`, and both pages of `spread-full` and
  `spread-margin`. Default `false`.
- `marks` (default `true`): crop marks and registration targets outside the bleed, in the registration
  colour; the MediaBox grows to hold them, TrimBox/BleedBox do not change. Set `false` if the printer asks for
  a PDF without marks (many POD services do).
- `fonts: {body, caption, title}`: fontconfig family names (`fc-list : family`); defaults Nanum Myeongjo /
  Nanum Gothic / Nanum Myeongjo. A family that is not installed silently falls back to another font, so
  check the proof. `type: {body_pt, caption_pt, title_pt}` (defaults 10 / 7.5 / 24); folios and the colophon
  use `caption_pt`, a `text` page heading is `body_pt + 4`, a subtitle `body_pt + 1`.
- On `margin` pages: `valign` (`top` default, `center`, `bottom`) moves the photo inside the space above the
  caption; `caption_align` (`left` default, `center`, `right`) aligns the caption.
- Korean text breaks only between words (like CSS `word-break: keep-all`); a word wider than its box is
  still broken.

## Both standards from one book

`out/` holds one render: rendering again with another `output.standard` replaces `interior.pdf`,
`report.json` and `check.json`. To deliver both, copy `out/interior.pdf` (and `check.json`) aside after the
first standard is green (e.g. to `delivery/interior-x1a.pdf` in the book directory), then change
`output.standard`/`output.icc`, `zg_render`, `zg_check`, and copy that one too. Both renders share `cache/`,
so nothing is fetched again (X-1a's CMYK copies live in `cache/cmyk/`). Alternatively keep a second book
directory with its own `book.yaml` and copy `cache/*.jpg` into it; a relative `output.icc` is then resolved
against that directory.

## Issue codes

Each issue has `code`, `level` (`info` | `warning` | `error`), `message` and, where it applies, `page` and
`photo`. Spread photos are reported once, on the left page of the spread.

| Code | Level | Meaning |
|---|---|---|
| `SPREAD_MOVED` | info | a blank was inserted so a spread starts on a left page |
| `PAGES_PADDED` | info | blanks added to reach the binding's page multiple |
| `LOW_DPI` / `BAD_DPI` | warning / error | effective resolution below 300 / 200 dpi |
| `GUTTER_CROSSING` | warning | a spread photo crosses the gutter on `perfect`/`hardcover` |
| `NO_ROOM` | error | margins, `gap_mm` or the caption leave a photo box under 5 mm (the page's photos are not placed), or a text page's body has less than one line of height (the body is not printed) |
| `NO_OUTPUT_PROFILE` | error | x-4 with `output.icc` unset: no output intent, fine for proofs, not for print |
| `DPI_ABOVE_NEED` | info | x-4 photos far above print resolution; may suggest a smaller `source.size` |
| `ORIENTATION_REENCODED` | warning | x-4 photo with an EXIF rotation, re-encoded once to embed it upright |
| `TEXT_CLIPPED` | warning | a caption (two lines) or other text box has more lines than fit; the rest is not printed |
| `DISPLAY_OUTPUT_PROFILE` | warning | `output.icc` is a display profile (e.g. the bundled sRGB), not the printer's |
| `NO_OUTPUT_INTENT`, `NO_DEST_PROFILE`, `NO_PDFX_VERSION`, `PDF_VERSION` | error | PDF check |
| `NO_TRIMBOX`, `TRIMBOX_SIZE`, `BLEED_SMALL` | error | PDF check |
| `NO_BLEEDBOX` | warning | PDF check |
| `IMAGE_COLORSPACE`, `IMAGE_SMASK`, `FONT_NOT_EMBEDDED` | error | PDF check |
| `TRANSPARENCY` | error on x-1a, warning on x-4 | PDF check (page and nested Form XObjects) |
| `VECTOR_COLORSPACE` | error | PDF check: RGB vector colour on x-1a; on x-4 a device colour space that does not match the output intent (DeviceRGB without an RGB intent, DeviceCMYK without a CMYK one) |

## Layout rules the renderer enforces

- Page 1 is a recto (right-hand) page. Spread templates must start on a verso; a blank is inserted otherwise.
- Bindings: `saddle` (pages × 4), `perfect` (× 2), `hardcover` (× 16), `layflat` (× 2); the page count is
  padded to the multiple.
- `perfect` (+4 mm) and `hardcover` (+3 mm) add gutter allowance to the inside margin; images crossing their
  gutter get a warning. `saddle` and `layflat` open flat enough for spreads.
- Effective resolution is computed from the embedded pixels vs placed size: < 300 dpi warns, < 200 fails.

## Colour and file size

- `output.icc` is resolved the same way by `zg_init_book` and `zg_render`. A value that looks like a path
  (contains `/` or `\`, starts with `~`, or ends in `.icc`/`.icm`) is a file; a relative path is resolved
  against the book directory, never the server's working directory. Anything else is a profile name, looked
  up as `<name>`, `<name>.icc` or `<name>.icm` in the Adobe and ColorSync profile folders and
  `/usr/share/color/icc` (`zinegen doctor` lists the press profiles it finds).
- PDF/X-1a converts each photo to CMYK once and downsamples it to `output.max_dpi` (default 350) at its
  largest placement; nothing is upsampled and the layout never moves. To make the PDF smaller, lower
  `output.max_dpi` (below 300 every downsampled photo is reported `LOW_DPI`) or `output.jpeg_quality`
  (default 95). `output.max_dpi: null` keeps every source pixel.
- PDF/X-4 embeds the source JPEGs unchanged, so neither setting shrinks it; only a smaller `source.size` does
  (`DPI_ABOVE_NEED` says when that is safe).

## Taste defaults (override on request)

- One idea per spread. Let strong single images breathe on a `margin` page facing a `blank` or a quiet image.
- Use `full-bleed`/`spread-full` sparingly (every 4-6 pages) for scale changes; never for images with the subject
  at the centre on perfect binding.
- Captions in 7-8 pt. On `margin` pages they sit at the bottom-left of the margin box; on `pair` pages directly
  under the two photos, across the full margin-box width; on `spread-margin` at the bottom right of the
  right-hand page. `full-bleed` and `spread-full` take no caption.
- A caption box holds two lines and drops the rest (whole lines, reported as `TEXT_CLIPPED`): keep `pair`
  and `margin` captions to two lines at the margin-box width (on A5 at 7.5 pt, about 170 Latin or 110 Korean
  characters) and check them on a higher-dpi proof.
- Keep margins asymmetric (inside > outside, bottom > top). Default A5: top 14 / bottom 16 / inside 16 / outside 12.
