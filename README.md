# crickey

Personal use only, at your own risk: Cricinfo's terms ban data-extraction tools.
crickey is not affiliated with Cricinfo, ESPNcricinfo or ESPN.

crickey will be an MCP server for answering cricket statistics questions from
Cricinfo Statsguru with proof links. This is an early project skeleton; the real
server implementation will be added by later issues.

## Development

```bash
uv sync
uv run ruff check .
uv run pytest
uv run crickey --help
```
