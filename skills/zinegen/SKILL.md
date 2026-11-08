---
name: zinegen
description: Typeset a photobook/zine from a Lightroom (cloud) album into a print-ready PDF/X with the zinegen CLI or MCP server. Use when asked to make a photobook, zine, 사진집, 포토북, 조판, or a print PDF from Lightroom photos.
---

# zinegen — agent-driven photobook typesetting

You are the typesetter. The user curates in Lightroom and reviews proofs; you write and iterate `book.yaml`.
Everything is derived from that one file. Never hand-edit the PDF or HTML.

## Tools

MCP (server name `zinegen`): `zg_spec_help`, `zg_list_albums`, `zg_album_photos`, `zg_init_book`, `zg_fetch`,
`zg_render`, `zg_proof` (returns spread images), `zg_check`, `zg_spine`.
CLI equivalents: `zinegen albums | init | fetch | render | proof | check | build | spine | presets | doctor`.
Lightroom browsing (ratings, filters, viewing candidates) comes from the `lightroom` MCP server (lr-mcp).

## Workflow

1. `zg_list_albums` → pick the album. `zg_album_photos` shows the album's own order, crop size (L/P) and captions.
2. Ask (or infer from the request) trim size, binding, print target. Offset/국내 인쇄소 → `pdf/x-1a` with a CMYK
   profile (`JapanColor2001Coated` is the Korean/Japanese default; `CoatedFOGRA39` for Europe; `USWebCoatedSWOP` for US).
   POD / Blurb-like services → `pdf/x-4` (RGB images are kept lossless, tagged Display P3).
3. `zg_init_book` drafts `books/<name>/book.yaml` (sequence = album order, templates auto-assigned) and
   `photos.json` (metadata). Read both.
4. Edit `book.yaml`: sequence, template per page, `focal` for cropped placements, captions, title/colophon text.
   The file starts with `version: 1` and is schema-checked: unknown keys and wrong types are rejected with the
   path of the mistake. Use `note` on a page for remarks; the renderer ignores it.
   Write caption/essay drafts yourself; keep them short and mark them clearly so the user can edit the YAML.
5. `zg_fetch` → `zg_render` → `zg_proof`. Look at every spread. Critique like an editor: pacing (rhythm of
   full-bleed vs margin pages), facing-page relationships, horizon alignment, repeated motifs, dead white space,
   subjects cut by the gutter, captions competing with images. Fix in `book.yaml`, re-render. Two or three passes.
6. `zg_check` must return `ok: true`. `zg_render` and `zg_check` return `issues` with a `code` (LOW_DPI,
   BAD_DPI, GUTTER_CROSSING, SPREAD_MOVED, PAGES_PADDED, ...), `level` and `page`; report warnings to the user.
7. Deliver `out/interior.pdf` and tell the user what to confirm with the printer: paper caliper for the spine
   (`zg_spine`), whether they accept PDF/X-1a or X-4, and the ICC profile used.

## Layout rules the renderer enforces

- Page 1 is a recto (right-hand) page. Spread templates must start on a verso; a blank is inserted otherwise.
- Page count is padded to the binding's multiple (saddle 4, perfect 2, hardcover 16).
- `perfect`/`hardcover` add gutter allowance to the inside margin; images crossing the gutter get a warning.
- Effective resolution is computed from the embedded pixels vs placed size: < 300 dpi warns, < 200 fails.

## Taste defaults (override on request)

- One idea per spread. Let strong single images breathe on a `margin` page facing a `blank` or a quiet image.
- Use `full-bleed`/`spread-full` sparingly (every 4-6 pages) for scale changes; never for images with the subject
  at the centre on perfect binding.
- Captions in 7-8 pt, bottom-left of the margin box; no captions on full-bleed pages.
- Keep margins asymmetric (inside > outside, bottom > top). Default A5: top 14 / bottom 16 / inside 16 / outside 12.
