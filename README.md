# crickey

crickey is an MCP server that answers cricket statistics questions from Cricinfo Statsguru with proof links.

crickey is for personal use at the user's own risk, Cricinfo's terms ban data-extraction tools, and crickey isn't affiliated with Cricinfo, ESPNcricinfo or ESPN.

## Start it

Docker image: `ghcr.io/syedhassaanahmed/crickey`, tagged as a version (`0.5.0`), a minor version (`0.5`) and `latest`. The commands use `0.5`, which gets its patch releases.

```sh
docker run -d --rm --read-only --name crickey -p 127.0.0.1:8765:8765 ghcr.io/syedhassaanahmed/crickey:0.5
```

Keep that container running before MCP clients connect; after a reboot, run it again. Check it: open http://127.0.0.1:8765/health in a browser; it shows `{"status":"ok"}`.

Stop it:

```sh
docker stop crickey
```

Update: pull the latest patch release, then stop the container and run it again.

```sh
docker pull ghcr.io/syedhassaanahmed/crickey:0.5
```

Pass a setting with `-e`:

```sh
docker run -d --rm --read-only --name crickey -p 127.0.0.1:8765:8765 -e CRICKEY_MIN_INTERVAL=15s ghcr.io/syedhassaanahmed/crickey:0.5
```

## Connect a client

Use this URL in your MCP client: `http://127.0.0.1:8765/mcp`. It isn't a web page, so a browser shows a "Missing session ID" error. If your client has a tool timeout, set it to 5 minutes.

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

Then start Copilot CLI with `--allow-all-mcp-server-instructions`:

```sh
copilot --allow-all-mcp-server-instructions
```

crickey's instructions tell the model to show each answer as crickey gives it and to copy its links exactly. Without the flag, Copilot CLI passes on only allowlisted servers' instructions, not crickey's, and answers can cut a proof link short or leave out a figure the question asks for. The flag passes on every MCP server's instructions, not just crickey's.

## Settings

| Variable | Default | Meaning |
|---|---:|---|
| `CRICKEY_MIN_INTERVAL` | `15s` | Average time between Cricinfo requests; valid `2s` through `168h`, with a warning below `15s`. |
| `CRICKEY_PORT` | `8765` | HTTP port inside the container; changing it needs a matching `-p` mapping. |
| `CRICKEY_HOST` | `127.0.0.1`; `0.0.0.0` in the image | Address to listen on. |
| `CRICKEY_URL` | none | Your instance's public URL, such as `https://crickey.example.com`, which crickey then answers to as well as localhost; a comma-separated list for more than one. |
| `CRICKEY_TOKEN` | none | Secret of at least 32 characters that clients send as `Authorization: Bearer <token>`. Required when other machines can reach crickey. |

After a burst of 4 Cricinfo requests, crickey waits 15 seconds between requests by default, so answers that need many are slow. The cache is only in memory, so it resets when the container stops. Run one container for all your clients so they share the cache, request spacing and block pause.

Debug stdio:

```sh
docker run -i --rm --read-only ghcr.io/syedhassaanahmed/crickey:0.5 stdio
```

## Host it in the cloud

To use your crickey from any device, run the image on a container platform that serves it over HTTPS, such as Azure Container Apps. It's still for your own use. Set:

- `CRICKEY_URL` to its URL, such as `https://crickey.example.com`;
- `CRICKEY_TOKEN` to a long random secret, such as the output of `openssl rand -hex 32`. crickey won't start without one once other machines can reach it.

The platform provides TLS: point it at port 8765, or set `CRICKEY_PORT` to the port it expects. `/health` needs no token, for the platform's health checks. Connect with the token:

```sh
copilot mcp add --transport http --timeout 300000 --header "Authorization: Bearer <token>" crickey https://crickey.example.com/mcp
```

In JSON, add `"headers": { "Authorization": "Bearer <token>" }` next to `"url"`. Your devices share the instance's request spacing and block pause, and Cricinfo may challenge a cloud IP more often than a home one. If your own reverse proxy forwards to crickey on `127.0.0.1`, set `CRICKEY_TOKEN` anyway: crickey can't tell that the proxy exposes it.

Contributors: see [AGENTS.md](AGENTS.md).
