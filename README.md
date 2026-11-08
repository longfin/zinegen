# zinegen

Agent-driven photobook typesetting: a Lightroom (cloud) album goes in, a print-ready **PDF/X** comes out.
One Python package provides a CLI, an MCP server and an agent skill, so Claude Code, Codex or any other
MCP-capable agent can act as the typesetter while you curate in Lightroom and review proofs.

No Adobe desktop app is required. Photos come from the Lightroom Partner API through
[lr-mcp](https://github.com/longfin/lr-mcp) as full-size JPEG renditions (Display P3, quality 92, 4:4:4),
which is enough for offset printing; see `docs/01-probe-results.md` for the measurements.

```
Lightroom album ──fetch──▶ cache/<asset>.jpg ──┐
book.yaml (edited by the agent / you) ─────────┴──render──▶ out/interior.pdf  (PDF/X-1a or PDF/X-4)
                                                           ──proof───▶ proof/spread-NN.png
                                                           ──check───▶ out/check.json
```

## What it does

- `book.yaml` is the single source of truth: trim size (presets or custom mm), bleed, binding, margins, fonts,
  output standard, ICC profile and the page sequence with one of nine templates per page
  (`title`, `blank`, `text`, `colophon`, `full-bleed`, `margin`, `pair`, `spread-full`, `spread-margin`).
- Page geometry is computed in millimetres, not left to CSS: every image placement reports its effective dpi
  and whether it crosses the gutter. Spreads are forced to start on a left-hand page; the page count is padded
  to the binding's multiple.
- **PDF/X-1a** (offset, 국내 인쇄소): images are converted to CMYK with LittleCMS (perceptual intent, black point
  compensation) and embedded without a second JPEG generation; text and rules are DeviceCMYK.
- **PDF/X-4** (POD, Blurb-style services): the original P3 JPEGs are embedded losslessly and tagged with their ICC
  profile; the output intent is still a CMYK press profile.
- Proof PNGs are composed as spreads so an agent can look at them (`zg_proof` returns images over MCP).
- A structural self-check (output intent, trim/bleed boxes, colour spaces, transparency, font embedding,
  resolution) runs with pikepdf. It is not a replacement for Acrobat/callas preflight.

## Install

```bash
brew install pango ghostscript            # WeasyPrint runtime, proof rendering (macOS; apt equivalents on Linux)
uv tool install lr-mcp && lr-mcp doctor   # Lightroom credentials and sign-in: follow the lr-mcp README
uv tool install .                         # or for development: uv venv && uv pip install -e ".[dev]"
zinegen doctor
```

CMYK ICC profiles cannot be redistributed, so zinegen looks for the usual ones on the machine
(Adobe installs, ColorSync) and otherwise takes a path in `output.icc`. ISO Coated v2 / PSO Coated v3
can be downloaded free from [ECI](https://www.eci.org/).

## Register with an agent

```bash
# Claude Code
claude mcp add zinegen -s user -- "$(which zinegen)" mcp
cp -r skills/zinegen ~/.claude/skills/zinegen
# Codex
codex mcp add zinegen -- "$(which zinegen)" mcp
cp -r skills/zinegen ~/.codex/skills/zinegen
```

Both agents also need the `lightroom` MCP server from lr-mcp for browsing and rating photos.

## Usage

```bash
zinegen albums autumn                                   # find the album id
zinegen init --album <album id> --dir books/autumn       # draft book.yaml + photos.json
$EDITOR books/autumn/book.yaml                           # sequence, templates, captions
zinegen build books/autumn                               # fetch + render + proof + check
zinegen spine --pages 48 --caliper 110                   # spine width from page count and paper caliper (µm)
zinegen presets                                          # trim presets and binding rules
```

MCP tools: `zg_spec_help`, `zg_list_albums`, `zg_album_photos`, `zg_init_book`, `zg_fetch`, `zg_render`,
`zg_proof`, `zg_check`, `zg_spine`.

## Book directory

```
books/<name>/
  book.yaml      the spec (the only thing to edit)
  photos.json    photo metadata from Lightroom
  cache/         renditions, index.json, cmyk/ conversions   (ignored by git)
  out/           interior.pdf, interior.html, report.json, check.json
  proof/         page-NNN.png, spread-NN.png
```

## Docs

- `AGENTS.md` — how an agent should work in this repo and with the tool
- `docs/00-research.md` — initial research (Korean)
- `docs/01-probe-results.md` — measured rendition quality and WeasyPrint PDF/X results (Korean)
- `docs/02-design.md` — design notes (Korean)
- `skills/zinegen/SKILL.md` — the agent skill

## Status

v0: interior PDF for a single album, A5 perfect binding verified end to end. Not yet: cover PDF with spine,
review GUI, upstreaming the Lightroom helpers to lr-mcp (issues #9–#11 there).

## License

MIT
