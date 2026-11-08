# Changelog

## 0.1.0 (unreleased)

First public version.

- `book.yaml` spec (format version 1, JSON Schema) with trim presets, four bindings and nine page templates.
- Lightroom album import through lr-mcp: album order, captions, cropped size, cached full-size renditions.
- PDF/X-1a (CMYK via LittleCMS) and PDF/X-4 (ICC-tagged RGB passthrough) output with WeasyPrint.
- Spread proofs (Ghostscript) and a structural PDF/X self-check (pikepdf) with coded issues.
- CLI, MCP server (`zg_*` tools) and an agent skill for Claude Code / Codex.
