# crickey

Personal use only, at your own risk: Cricinfo's robots.txt disallows results pages
and its terms ban data-extraction tools. crickey is not affiliated with Cricinfo,
ESPNcricinfo or ESPN.

crickey will be an MCP server for answering cricket statistics questions from
Cricinfo Statsguru with proof links. This is an early project skeleton; the real
server implementation will be added by later issues.

## Development

```powershell
uv sync
uv run ruff check .
uv run pytest
uv run crickey --help
```
