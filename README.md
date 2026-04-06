# MCP Server — Baumvergleich.de

MCP (Model Context Protocol) server exposing Baumvergleich.de data to AI assistants.

## Tools

1. **find_tree_care_companies** — Find Baumpflege/Baumfällung companies in a city
2. **get_tree_care_prices** — Get typical prices for tree care services
3. **get_tree_protection_rules** — Get Baumschutzverordnung for a city

## Setup

```bash
pip install mcp supabase
python server.py
```

## Claude Desktop Config

Add to `~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "baumvergleich": {
      "command": "python",
      "args": ["C:/Claude Code works/Holding/mcp-baumvergleich/server.py"]
    }
  }
}
```

## Publishing

To publish to MCP registries (Smithery, MCPT, OpenTools):
1. Create a GitHub repo
2. Add server metadata
3. Submit to registries
