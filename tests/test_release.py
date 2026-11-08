"""The package, the Claude Code plugin manifest and the skill must carry the same version."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def skill_frontmatter() -> dict:
    text = (ROOT / "skills" / "zinegen" / "SKILL.md").read_text()
    assert text.startswith("---\n")
    return yaml.safe_load(text.split("---\n", 2)[1])


def test_versions_agree():
    pkg = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    skill = skill_frontmatter()
    assert plugin["version"] == pkg
    assert skill["metadata"]["version"] == pkg


def test_plugin_and_marketplace_consistent():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    entry = market["plugins"][0]
    assert entry["name"] == plugin["name"] == "zinegen"
    assert entry["source"] == "./"
    mcp = json.loads((ROOT / ".mcp.json").read_text())
    assert mcp["mcpServers"]["zinegen"] == {"command": "zinegen", "args": ["mcp"]}


def test_skill_frontmatter_follows_spec():
    fm = skill_frontmatter()
    assert fm["name"] == "zinegen"               # must match the directory name
    assert 1 <= len(fm["description"]) <= 1024
    assert len(fm.get("compatibility", "")) <= 500
    assert all(isinstance(v, str) for v in fm["metadata"].values())
