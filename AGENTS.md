# AGENTS.md

Guidance for AI agents working in this repository (Claude Code, Codex and others).

## What this project is

`zinegen` turns a Lightroom (cloud) album into a print-ready PDF/X. The agent is the typesetter: it edits
`books/<name>/book.yaml`, renders, looks at the spread proofs, and iterates. The human curates photos in
Lightroom and reviews proofs. See `README.md` for the pipeline and `skills/zinegen/SKILL.md` for the
typesetting workflow and taste rules.

## Layout

```
src/zinegen/
  lr/compat.py   Lightroom helpers missing from lr-mcp (album order, captions, cached renditions)
  presets.py     trim presets, binding rules, spine width
  spec.py        book.yaml loading, JSON Schema validation, skeleton drafting
  schema/        book.schema.json (book.yaml format, version 1)
  issues.py      issue codes shared by report.json and check.json
  layout.py      page geometry in mm, dpi/gutter report
  render.py      HTML -> WeasyPrint PDF/X, pikepdf post-processing
  color.py       ICC conversion (Pillow + LittleCMS), gamut check
  proof.py       Ghostscript page PNGs -> spread PNGs
  check.py       structural PDF/X self-check
  cli.py         `zinegen` subcommands
  server.py      MCP server (`zinegen mcp`), tools prefixed `zg_`
  templates/     book.html.j2, fallback sRGB2014.icc
skills/zinegen/  agent skill
docs/            research, probe results, design (Korean)
probe/           reproducible experiments (not part of the package)
books/           sample book specs; cache/out/proof are git-ignored
tests/           pytest (no network)
```

## Setup and commands

```bash
uv venv && . .venv/bin/activate && uv pip install -e ".[dev]"
zinegen doctor                      # pango, ghostscript, ICC profiles, Lightroom sign-in
python -m pytest -q                 # unit tests (spec, layout, presets)
zinegen build books/danpung         # end-to-end on the sample album (needs Lightroom sign-in)
```

macOS: WeasyPrint needs `brew install pango`; `render.py` sets `DYLD_FALLBACK_LIBRARY_PATH` to the Homebrew
lib dir automatically. Ghostscript (`gs`) is required for proofs.

## Rules

- **Frozen formats** (see `docs/03-schemas.md`): `book.yaml` (`version: 1`, `schema/book.schema.json`),
  `photos.json`, `out/report.json`, `out/check.json`. Add fields freely; renaming or removing one, or changing
  a meaning, bumps that format's version and needs a migration note in the doc. New issue codes go in `issues.py`.

- **`book.yaml` is the only editable artefact of a book.** Never patch `out/interior.pdf` or `interior.html`.
  If the renderer cannot express something, extend `layout.py`/`spec.py` and add a template.
- **Keep geometry explicit.** All image placement is computed in `layout.py` in millimetres so that dpi and
  gutter crossing can be reported. Do not move placement into CSS (`object-fit` etc.).
- **PDF/X invariants** (checked by `check.py`): an output intent with an embedded profile, TrimBox and BleedBox
  on every page, CMYK/Gray images only for X-1a, ICC-tagged RGB for X-4, no transparency on X-1a, all fonts
  embedded. Any change to `render.py` or the template must keep `zinegen check` green on both standards.
- **Never recompress a source JPEG twice.** X-1a converts P3 -> CMYK once (cached in `cache/cmyk/`); X-4 embeds
  the original bytes. Keep `optimize_images=False` and the DCT passthrough.
- **Lightroom access is read-only** and goes through `lr_mcp.client`. Do not add write calls. Do not print or
  commit anything from `~/.config/lr-mcp/` (tokens, client secret).
- **ICC profiles and Apple/Adobe fonts are not redistributable.** Only `sRGB2014.icc` (from WeasyPrint) is bundled.
  Resolve press profiles at run time (`color.find_profile`) and prefer OFL fonts (Nanum, Noto, Pretendard) as defaults.
- Functions that lr-mcp may adopt later live in `lr/compat.py` and must stay importable with only `lr_mcp.client`
  as a dependency, so they can be upstreamed unchanged (lr-mcp issues #9, #10, #11).
- Tests must not touch the network. Anything that needs Lightroom belongs in a manual `build` run, not in pytest.
- Commit messages: short imperative subject, body explaining why. Do not commit `books/*/cache`, `out`, `proof`,
  PDFs or PNGs (see `.gitignore`).

## When typesetting a book (not developing the tool)

Load the skill in `skills/zinegen/SKILL.md` and follow it. In short: pick the album, draft with `zg_init_book`,
edit `book.yaml`, `zg_fetch` → `zg_render` → `zg_proof`, critique every spread, iterate two or three times,
finish with `zg_check` green and report warnings (low dpi, gutter crossing, padded pages) to the user.

## Open work

- Cover PDF (spine from `zinegen spine`, wrap-around layout, separate PDF/X).
- Local review GUI: spread viewer with comments the agent can read; no editing.
- Upstream `lr/compat.py` to lr-mcp.
- Real preflight comparison (Acrobat Pro / callas) and fixes for anything it flags.
