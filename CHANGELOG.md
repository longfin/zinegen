# Changelog

## 0.2.0 — 2026-11-14

**Rebuild PDFs made with 0.1.0.** WeasyPrint re-encoded every embedded JPEG in 0.1.0 (a second lossy
generation on PDF/X-1a, and on PDF/X-4 the source bytes were not kept), and PDF/X-4 tagged Display P3 photos
as sRGB. With the default `marks: true`, 0.1.0 also drew the crop marks in DeviceRGB inside a transparency
group, inside the bleed, and `check` did not notice. All of this is fixed; render again to get the intended
output.

### Changed

- PDF/X-1a photos are downsampled to 350 dpi at their largest placement by default, inside the single CMYK
  conversion (one JPEG generation, never upsampled, layout unchanged). A 20-photo A5 book went from 338 MB to
  133 MB. New `output.max_dpi` sets the target; `output.max_dpi: null` restores full resolution.
  `output.jpeg_quality` is the other size lever.
- CMYK JPEGs no longer embed the press profile (the output intent carries it), which saves the size of the
  profile (often 0.5 MB or more) per photo.
- A relative `output.icc` path is resolved against the book directory, in `zinegen init`/`zg_init_book` and in
  render, instead of the working directory. A value is a path only if it contains a separator, starts with
  `~` or ends in `.icc`/`.icm`; anything else is a profile name looked up in the profile directories, so a
  file in the book directory cannot shadow an installed profile.
- Render fails, before writing anything, when `output.icc` is set but cannot be resolved or is not a readable
  profile (0.1.0 silently wrote a PDF/X-4 without an output intent). With `output.icc` unset on PDF/X-4, render
  still runs for proofing and reports `NO_OUTPUT_PROFILE`, so `check` is not ok.
- Render fails, and removes the PDF, when a placed photo is not embedded exactly as prepared.
- `LOW_DPI`, `BAD_DPI` and `GUTTER_CROSSING` are reported once per spread photo, on the left page, instead of
  once per page.
- `photos.json` `keywords` is a list of strings (0.1.0 wrote `[true, …]`). Re-run `init` into a scratch directory
  and copy `photos.json` over to refresh an old book (see `docs/03-schemas.md`).
- `zinegen fetch` (and `build`) exits 1 when `book.yaml` references photo ids that are not in Lightroom, and
  lists them.
- `zinegen init` prints the absolute path of `book.yaml`; an explicit `--icc` that cannot be resolved (or is not
  CMYK for `pdf/x-1a`) fails init; an `--album` that is not a 32-hex id fails at once instead of retrying.
- `zinegen spine` / `zg_spine` reject non-positive pages or caliper, a negative board and unknown bindings.
- Lightroom errors are printed as `error: …` (exit 2) instead of a traceback; render errors likewise.
- Trim preset labels corrected: `SIN46` is 신사륙판 and `CROWN` (152×225, size unchanged) is 신국판 size.
  Preset names are case-insensitive, so `8x10`, `10x8` and `7x7` now resolve.

### Fixed

- Crop marks (`marks: true`, the default) are PDF/X-safe and outside the bleed, as the schema says. 0.1.0 used
  WeasyPrint's `marks: crop cross`, which draws an SVG with `fill="transparent" stroke="black"`: a Form XObject
  with a transparency group, an ExtGState with `ca 0` and DeviceRGB strokes, inside the bleed (MediaBox =
  BleedBox), on both standards. zinegen now draws the marks itself in the registration colour (Separation
  `/All`; DeviceCMYK alternate for a CMYK output intent, DeviceGray otherwise), without transparency, starting
  1 mm outside the BleedBox, and enlarges the MediaBox to hold them.
- `check` looks into nested Form XObjects and tiling patterns (transparency groups, ExtGState alpha, soft
  masks and blend modes, images, fonts) and at the colour operators of every content stream, so RGB vector
  colour on PDF/X-1a is reported (`VECTOR_COLORSPACE`) instead of passing.
- `check` also inspects inline images (`BI … EI`, abbreviated `/CS /RGB`, `/G`, `/CMYK`, `/I` and named
  colour spaces), annotation appearance streams and Type 3 glyph procedures; a Type 3 font is no longer
  reported as `FONT_NOT_EMBEDDED`. On PDF/X-4 a device colour space must match the output intent: DeviceRGB is
  reported without an RGB intent (a Gray intent passed before) and DeviceCMYK without a CMYK one. A shared bad
  colour-space or shading resource is reported once, with the page count, instead of once per page.
- `bleed_mm` above 3.53 mm no longer fails `check` with `BLEED_SMALL`: WeasyPrint caps the BleedBox at 10 pt
  although it lays the page out for the full bleed; render now sets the BleedBox to the real bleed (with or
  without marks), and `BLEED_SMALL` checks all four sides.
- Text on PDF/X-4 with an RGB (or Gray) output intent, e.g. the bundled sRGB proof profile, was DeviceCMYK,
  which PDF/X-4 only allows with a CMYK intent. It is now a neutral grey in the output-intent profile
  (ICCBased) whose L* matches how the K tint proofed before (caption/colophon at 85 % ≈ L* 33, folio at 70 % ≈
  L* 46, solved through the profile with LittleCMS); solid black is the profile's true black (RGB 0 0 0) rather
  than the L* 12 grey a K-only solid proofed as. CMYK intents and PDF/X-1a keep pure K. **PDF/X-4 PDFs rendered
  earlier with an RGB or Gray output intent now fail `check` with `VECTOR_COLORSPACE`; render them again.**
- `VECTOR_COLORSPACE` issues for a colour-space or shading resource shared by several pages are reported once,
  with `page` set to the first page and the page count in the message (they were repeated per page).
- Unused Lab/ICCBased colour spaces are pruned in Form XObjects as well as on pages.
- A text page whose body gets less than one line of height (margins, heading, type size) is a `NO_ROOM` error
  and the body is not drawn, instead of a one-line box spilling into the bottom margin. Fixed-height boxes
  (title, subtitle, heading, colophon, folio, caption) still keep at least one line, so large type prints.
- Rendering a book whose id the last fetch reported missing, but that a `photos.json` written after that fetch
  lists (the album was drafted again), asks for a fetch instead of a `book.yaml` change; the "replace the id"
  message says that a fetch re-checks.
- Render fails, instead of drawing misplaced crop marks, if pages ever had different trim sizes.
- `zinegen spine` / `zg_spine` compute the width for the rounded page count they announce (23 pages perfect
  bound were reported "rounded to 24" with the 23-page width; hardcover 24 → 32 likewise).
- Text that does not fit its box is cut between lines instead of through the middle of a glyph (every text box
  holds a whole number of lines; a caption two), and reported as `TEXT_CLIPPED`.
- Korean text breaks between words only (`word-break: keep-all`, which WeasyPrint ignores, is emulated with
  word joiners); a word wider than its box still breaks (`overflow-wrap: anywhere`).
- Rendering a book whose photo id the last fetch reported missing says that the id is not in the album or the
  catalog, instead of asking for another fetch.
- An album id that is well formed but not in the catalog is reported in a sentence, not as Lightroom's raw 404
  response.
- `zg_proof` with only out-of-range spread numbers is an error (and, when the PDF can be read, does not run
  Ghostscript).
- MCP tools reject unknown arguments (they were ignored) and their input schemas say so
  (`additionalProperties: false`); argument errors no longer end with a pydantic documentation URL.
- ICC error messages: no `<name>.icc.icc` candidate for a name that already ends in `.icc`, and an absolute
  path is not printed twice.
- `zinegen init` / `zg_init_book` say when the default book directory name differs from the album name
  (characters other than letters, digits, `-` and `_` become `_`).
- `pair` captions sit directly under the two photos instead of in the bottom margin, where they ran into the
  page number.
- Photos with an EXIF orientation are placed upright with the right proportions on both standards (layout used
  the unrotated size). On PDF/X-4 such a photo is rotated and encoded once and reported `ORIENTATION_REENCODED`.
- MCP tool errors carry their cause (invalid `book.yaml` with the path of each mistake, missing files, steps run
  out of order, Ghostscript output) instead of a bare "Error executing tool". Messages name `zg_*` tools, not CLI
  commands, and drop the Lightroom catalog id.
- The MCP server never writes anything but JSON-RPC to stdout.
- `zg_init_book` returns the absolute path of `book.yaml`; an existing `book.yaml` (without `force=true`) and an
  unknown `icc` (or a non-CMYK one for `pdf/x-1a`) are tool errors.
- `zg_fetch` fetches photos referenced outside the album by id, as `zinegen fetch` did.
- `zg_album_photos` and `zg_init_book` reject an album name instead of retrying for about 30 s.
- `zg_check` and `zg_proof` explain a missing or truncated `out/interior.pdf` instead of failing obscurely.
- Rendition downloads: `errors` lists only photos that failed every retry, `cache/index.json` is written after
  each download (an interrupted fetch keeps its progress), files are written atomically, and duplicate ids are
  downloaded once.
- The MCP initialize response carries the package version.

### Added

- `output.max_dpi` (see above); `report.json` gains `max_dpi` and `images[].source_px`.
- Issue codes `NO_ROOM` (margins, `gap_mm` or caption leave no room for a photo; the page's photos are not placed),
  `NO_OUTPUT_PROFILE`, `DPI_ABOVE_NEED` (PDF/X-4 photos far above print resolution, with a `source.size` hint
  when it is safe) and `ORIENTATION_REENCODED`.
- Issue codes `TEXT_CLIPPED`, `VECTOR_COLORSPACE` and `DISPLAY_OUTPUT_PROFILE` (a warning when `output.icc` is
  not a printer-class profile, e.g. the bundled sRGB on PDF/X-4).
- The skill documents `page_numbers` (and which pages get folios), `marks`, `valign`, `caption_align`, the
  font and type keys, and how to deliver both PDF/X standards from one book directory.
- Trim presets `SINGUK` (신국판, 152×225) and `CROWN176` (크라운판, 176×248).
- `zg_proof` returns at most 12 spreads per call and says which spreads were left out and how to request them,
  and labels each image with its pages.
- `zg_fetch` returns `outside_album`, `missing` and a `warning` when ids are missing.
- `zinegen --version`; parameter descriptions and enums (`standard`, `binding`) in the MCP tool schemas.
- The PDF/X-1a tests run on CI with Ghostscript's CMYK profile.

## 0.1.0 — 2026-11-08

First public version.

- `book.yaml` spec (format version 1, JSON Schema) with trim presets, four bindings and nine page templates.
- Lightroom album import through lr-mcp: album order, captions, cropped size, cached full-size renditions.
- PDF/X-1a (CMYK via LittleCMS) and PDF/X-4 (ICC-tagged RGB passthrough) output with WeasyPrint.
- Spread proofs (Ghostscript) and a structural PDF/X self-check (pikepdf) with coded issues.
- CLI, MCP server (`zg_*` tools) and an agent skill for Claude Code / Codex.
- Installable as a Claude Code plugin (`/plugin install zinegen --marketplace longfin/zinegen`) and through
  `npx skills add longfin/zinegen`.
