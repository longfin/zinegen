# zinegen

[![ci](https://github.com/longfin/zinegen/actions/workflows/ci.yml/badge.svg)](https://github.com/longfin/zinegen/actions/workflows/ci.yml)

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
- **PDF/X-1a** (offset, 국내 인쇄소): each photo is converted to CMYK with LittleCMS (perceptual intent, black
  point compensation) and downsampled to `output.max_dpi` (350 by default) in the same single pass, so the PDF
  holds exactly one JPEG generation; text and rules are DeviceCMYK.
- **PDF/X-4** (POD, Blurb-style services): the original P3 JPEGs are embedded byte for byte and tagged with their
  ICC profile (a photo with an EXIF rotation is the one exception: it is rotated and encoded once); the output
  intent is still the press profile from `output.icc`. With a CMYK press profile text is pure K (DeviceCMYK);
  an RGB output intent (the bundled sRGB) is for proofs only: text is then RGB black and greys in that profile,
  which a printer converts to four-colour black, so set a CMYK `output.icc` for press.
- Proof PNGs are composed as spreads so an agent can look at them (`zg_proof` returns images over MCP).
- A structural self-check (output intent, trim/bleed boxes, colour spaces, transparency, font embedding,
  resolution) runs with pikepdf. It is not a replacement for Acrobat/callas preflight.

**New here?** Follow [docs/getting-started.md](docs/getting-started.md): it takes a Claude Code or Codex
subscriber from zero to a first PDF, and the agent can run it for you.

## Install

```bash
brew install pango ghostscript            # WeasyPrint runtime, proof rendering (macOS; apt equivalents on Linux)
uv tool install lr-mcp && lr-mcp doctor   # Lightroom credentials and sign-in: follow the lr-mcp README
uv tool install zinegen                   # from PyPI; for development: git clone, uv venv && uv pip install -e ".[dev]"
zinegen doctor
```

`uv tool install` puts `zinegen` in uv's tool directory; if `which zinegen` finds nothing, run
`uv tool update-shell` and open a new terminal. The agent integrations below start `zinegen mcp` by name, so
this must work in the shell you start the agent from.

CMYK ICC profiles cannot be redistributed, so zinegen looks for the usual ones on the machine
(Adobe installs, ColorSync, `/usr/share/color/icc`) by name, e.g. `output.icc: JapanColor2001Coated`, and
otherwise takes a path in `output.icc` (a relative path is resolved against the book directory).
ISO Coated v2 / PSO Coated v3 can be downloaded free from [ECI](https://www.eci.org/).

## Register with an agent

**`zinegen` must be on `PATH` first** (`uv tool install zinegen`, above). The plugin and the skill do not
install it: the plugin's `.mcp.json` only runs `zinegen mcp`, and the server fails to start if the command is
not found. Then pick one:

```bash
# Claude Code: the plugin adds the skill and registers the `zinegen` MCP server in one step
claude plugin marketplace add longfin/zinegen     # the marketplace is named `longfin`
claude plugin install zinegen@longfin

# Any agent that reads SKILL.md folders (Claude Code, Codex, Cursor, ...): skill only
npx skills add longfin/zinegen
codex mcp add zinegen -- "$(which zinegen)" mcp          # Codex still needs the MCP server registered
```

Without either tool, clone the repo and copy `skills/zinegen` into `~/.claude/skills/` or `~/.codex/skills/` and register the server
with `claude mcp add zinegen -s user -- "$(which zinegen)" mcp`.

Both agents also need the `lightroom` MCP server from lr-mcp for browsing and rating photos.

## Usage

```bash
zinegen albums autumn                                   # find the album id (--counts adds photo counts)
zinegen init --album <album id> --dir books/autumn       # draft book.yaml + photos.json
$EDITOR books/autumn/book.yaml                           # sequence, templates, captions
zinegen build books/autumn                               # fetch + render + proof + check
zinegen spine --pages 48 --caliper 110                   # spine width from page count and paper caliper (µm)
zinegen presets                                          # trim presets and binding rules
```

A complete annotated spec is in [`examples/book.example.yaml`](examples/book.example.yaml).

MCP tools: `zg_spec_help`, `zg_list_albums`, `zg_album_photos`, `zg_init_book`, `zg_fetch`, `zg_render`,
`zg_proof`, `zg_check`, `zg_spine`.

## Book directory

```
books/<name>/
  book.yaml      the spec (the only thing to edit)
  photos.json    photo metadata from Lightroom
  cache/         renditions, index.json, cmyk/ (x-1a conversions), upright/ (x-4 rotated copies)
  out/           interior.pdf, interior.html, report.json, check.json
  proof/         page-NNN.png, spread-NN.png
```

## Docs

- `docs/getting-started.md` — zero-to-first-book guide for Claude Code / Codex users
- `AGENTS.md` — how an agent should work in this repo and with the tool
- `docs/00-research.md` — initial research (Korean)
- `docs/01-probe-results.md` — measured rendition quality and WeasyPrint PDF/X results (Korean)
- `docs/02-design.md` — design notes (Korean)
- `skills/zinegen/SKILL.md` — the agent skill

## Status

Alpha (0.2.0). Interior PDF for a single album, A5 perfect binding verified end to end against a real album;
the PDF/X output passes the built-in structural check but has not yet been through Acrobat/callas preflight.
Not yet: cover PDF with spine, review GUI, moving the Lightroom helpers onto an lr-mcp API (longfin/lr-mcp#19, #20).
See [CHANGELOG.md](CHANGELOG.md).

## License

MIT. Third-party material and external tools are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This project is not affiliated with, endorsed by, or sponsored by Adobe. Adobe, Lightroom and InDesign are
either registered trademarks or trademarks of Adobe in the United States and/or other countries.
