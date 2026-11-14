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
  lr/compat.py   Lightroom album/photo data and rendition cache as Python values (lr-mcp has them as tool text)
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
examples/        annotated book.example.yaml
books/           your books (git-ignored: they reference a private catalog)
tests/           pytest (no network)
```

## Setup and commands

```bash
uv venv && . .venv/bin/activate && uv pip install -e ".[dev]"
zinegen doctor                      # pango, ghostscript, ICC profiles, Lightroom sign-in
python -m pytest -q                 # spec/layout/presets, real renders + check on both standards, MCP server, lr compat, release checks
zinegen build books/<name>          # end-to-end on a real album (needs Lightroom sign-in); books/ is git-ignored
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
- **Korean keep-all is not CSS.** WeasyPrint 70 silently drops `word-break: keep-all`, so the `keep_all` Jinja
  filter (`render.keep_all`, U+2060 WORD JOINER inside Hangul words) plus `overflow-wrap: anywhere` on `.t`
  does it. Do not move it back to CSS.
- **PDF/X invariants** (checked by `check.py`): an output intent with an embedded profile, TrimBox and BleedBox
  on every page, CMYK/Gray images only for X-1a, ICC-tagged RGB for X-4, no transparency on X-1a, all fonts
  embedded. Any change to `render.py` or the template must keep `zinegen check` green on both standards.
  `check.py` walks nested Form XObjects and content-stream colour operators, so do not rely on WeasyPrint
  features that draw through SVG (its `@page { marks }` produced DeviceRGB in a transparency group): crop
  marks are drawn by `render.add_marks` from `layout.crop_marks`.
- **Never recompress a source JPEG twice.** X-1a decodes each photo once, applies EXIF orientation, downsamples
  it to `output.max_dpi`, converts P3 -> CMYK and encodes it once (cached in `cache/cmyk/`); X-4 embeds the
  original bytes. The one exception is an X-4 photo with an EXIF orientation tag: it is rotated and encoded once
  (`cache/upright/`) and reported `ORIENTATION_REENCODED`. WeasyPrint must pass the prepared files through
  untouched: `render.render` lays out with `HTML.render(**opts)` (needed for `TEXT_CLIPPED`) and then calls
  `Document.write_pdf(...)`; keep `jpeg_quality=None, dpi=None, optimize_images=False` in both calls, and
  `image-orientation: none` on the photo `<img>` in the template (without any one of them WeasyPrint re-encodes
  JPEGs: any `jpeg_quality` re-encodes all, `dpi` those above it, and the default `from-image` every JPEG
  with an EXIF block, i.e. every Lightroom rendition). `render._verify_images` fails the render when a placed
  photo's prepared file is not embedded byte for byte.
- **Lightroom access is read-only** and goes through `lr_mcp.client`. Do not add write calls. Do not print or
  commit anything from `~/.config/lr-mcp/` (tokens, client secret).
- **ICC profiles and Apple/Adobe fonts are not redistributable.** Only `sRGB2014.icc` (from WeasyPrint) is bundled.
  Resolve press profiles at run time (`color.find_profile`) and prefer OFL fonts (Nanum, Noto, Pretendard) as defaults.
- Functions that lr-mcp may adopt later live in `lr/compat.py` and must stay importable with only `lr_mcp.client`
  as a dependency, so they can be upstreamed unchanged. lr-mcp 0.4 has album order, captions/keywords/cropped
  size, a download cache and album counts, but only inside its MCP tools; what remains is a structured API
  (longfin/lr-mcp#19) and a downloader zinegen could use (longfin/lr-mcp#20).
- Tests must not touch the network. `tests/test_render.py` renders synthetic JPEGs through the real pipeline on
  both standards: PDF/X-1a uses any CMYK profile found (`tests/conftest.py`: `ZINEGEN_TEST_CMYK_ICC`, an
  installed press profile, or Ghostscript's `default_cmyk.icc`) and is skipped without one, except on CI, where
  `ZINEGEN_REQUIRE_CMYK=1` makes that a failure. `tests/test_server.py` drives the MCP tools in-process with
  Lightroom mocked. Anything that needs Lightroom is a manual `build` run.
- **One version, three places.** `pyproject.toml`, `.claude-plugin/plugin.json` and `metadata.version` in
  `skills/zinegen/SKILL.md` must agree (`tests/test_release.py` checks). To release: bump all three, date the
  CHANGELOG entry, commit, `git tag vX.Y.Z && git push --tags`, then `uv build` and `gh release create vX.Y.Z dist/*`. Publishing the GitHub release triggers
  `publish.yml`, which uploads to PyPI through trusted publishing (no token in the repo or on any machine).
  The repo root is both the plugin and a one-entry marketplace (`.claude-plugin/marketplace.json`, `.mcp.json`);
  `claude plugin validate .` must pass.
- Lint with `ruff check .` (config in pyproject); CI runs ruff, pytest and a wheel build on Ubuntu.
- Commit messages: short imperative subject, body explaining why. Do not commit `books/*/cache`, `out`, `proof`,
  PDFs or PNGs (see `.gitignore`).

## When typesetting a book (not developing the tool)

Load the skill in `skills/zinegen/SKILL.md` and follow it. In short: pick the album, draft with `zg_init_book`,
edit `book.yaml`, `zg_fetch` → `zg_render` → `zg_proof`, critique every spread, iterate two or three times,
finish with `zg_check` green and report warnings (low dpi, gutter crossing, padded pages) to the user.

## Open work

- Cover PDF (spine from `zinegen spine`, wrap-around layout, separate PDF/X).
- Local review GUI: spread viewer with comments the agent can read; no editing.
- Replace `lr/compat.py` with lr-mcp functions once lr-mcp exposes a structured API (longfin/lr-mcp#19) and a
  downloader with atomic writes, an incremental sidecar and id-based names (longfin/lr-mcp#20); see #23.
- Real preflight comparison (Acrobat Pro / callas) and fixes for anything it flags. `check.py` is a structural
  self-check (boxes, output intent, colour spaces, transparency, fonts); it does not replace a real preflight.
