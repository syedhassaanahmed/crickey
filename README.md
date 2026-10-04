# crickey

crickey is an MCP server that answers cricket statistics questions from Cricinfo Statsguru with proof links.

crickey is for personal use at the user's own risk, Cricinfo's terms ban data-extraction tools, and crickey isn't affiliated with Cricinfo, ESPNcricinfo or ESPN.

## Start it

Docker image: `ghcr.io/syedhassaanahmed/crickey`, tagged as a version (`0.1.0`), a minor version (`0.1`) and `latest`. The commands use `0.1`, which gets its patch releases.

```sh
docker run -d --rm --read-only --name crickey -p 127.0.0.1:8765:8765 ghcr.io/syedhassaanahmed/crickey:0.1
```

Keep that container running before MCP clients connect; after a reboot, run it again. Check it: open http://127.0.0.1:8765/health in a browser; it shows `{"status":"ok"}`.

Stop it:

```sh
docker stop crickey
```

Update: pull the latest patch release, then stop the container and run it again.

```sh
docker pull ghcr.io/syedhassaanahmed/crickey:0.1
```

Pass a setting with `-e`:

```sh
docker run -d --rm --read-only --name crickey -p 127.0.0.1:8765:8765 -e CRICKEY_MIN_INTERVAL=15s ghcr.io/syedhassaanahmed/crickey:0.1
```

## Connect a client

Use this URL: `http://127.0.0.1:8765/mcp`. If your client has a tool timeout, set it to 5 minutes.

```json
{
  "mcpServers": {
    "crickey": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

Copilot CLI:

```sh
copilot mcp add --transport http --timeout 300000 crickey http://127.0.0.1:8765/mcp
```

## Settings

| Variable | Default | Meaning |
|---|---:|---|
| `CRICKEY_MIN_INTERVAL` | `15s` | Minimum time between Cricinfo requests; valid `2s` through `168h`, with a warning below `15s`. |
| `CRICKEY_MAX_RETRIES` | `3` | Retries after the first request; use `0` to turn retries off. |
| `CRICKEY_BLOCK_PAUSES` | `5m,15m,1h,4h,24h` | Pause schedule after a block; comma-separated durations, each up to `168h`. |
| `CRICKEY_MAX_PAGES` | `4` | Maximum Statsguru result pages per tool call. |
| `CRICKEY_CACHE_MAX_MB` | `64` | In-memory page cache size in MB. |
| `CRICKEY_RECENT_TTL` | `1h` | Cache time for pages that include recent matches; up to `168h`. |
| `CRICKEY_PORT` | `8765` | HTTP port inside the container; changing it needs a matching `-p` mapping. |
| `CRICKEY_IN_CONTAINER` | `0` | Set to `1` by the image so it can listen inside Docker. |

Requests are slow because crickey waits 15 seconds between Cricinfo requests by default. The cache is only in memory, so it resets when the container stops. Run one container for all your clients so they share the cache, request spacing and block pause.

Debug stdio:

```sh
docker run -i --rm --read-only ghcr.io/syedhassaanahmed/crickey:0.1 stdio
```

Contributors: see [AGENTS.md](AGENTS.md).
