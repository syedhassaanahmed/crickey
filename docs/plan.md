# Plan: `crickey`, an MCP server for Cricinfo Statsguru

## Overview
crickey is an MCP server that answers cricket statistics questions using only Cricinfo's Statsguru, with Cricinfo links in every answer so people can check the numbers and share them. It covers Statsguru's basic and advanced filters.

It runs as a Streamable HTTP server on localhost: you run it natively, and friends run the public Docker image that CI publishes to GitHub Container Registry. stdio is kept for debugging only. The server keeps no state on disk.

- **Decisions** are in [decisions.md](decisions.md); this plan refers to them as D1–D26. The golden questions are listed there too.
- **Research facts** are in [research.md](research.md); this plan refers to its sections as R1–R16.

## Versions
Use the latest stable releases listed in R15 (D22).
- `pyproject.toml` sets `requires-python` to R15's Python minor version and, for each dependency, a lower bound at its R15 version and an upper bound below its next major version.
- Actions are pinned to the commit SHAs of R15's releases.

## Architecture
```mermaid
flowchart LR
    you["Your Copilot CLI"]
    friends["Friends' MCP clients"]
    debug["Debugging<br/>MCP Inspector · tests"]

    subgraph server["crickey MCP server (one per person, state in memory only)"]
        tools["Tools<br/>leaderboard · better_than_player · player_record<br/>find_player · query_stats"]
        logic["Shared logic<br/>metric registry · name and period resolvers<br/>proof-link builder · answer renderer"]
        query["Query spec + built-in ID tables<br/>URL compiler: standard form, pinned dates"]
        fetch["Polite fetcher<br/>spacing · retries · block pause<br/>in-memory cache · page cap"]
        parse["Parsers<br/>pandas tables with exact columns"]
        tools --> logic --> query --> fetch
        fetch --> parse --> logic
    end

    statsguru[("stats.cricinfo.com<br/>Statsguru")]

    you -->|"Streamable HTTP, native<br/>127.0.0.1:8765/mcp"| tools
    friends -->|"Streamable HTTP, Docker<br/>127.0.0.1:8765/mcp"| tools
    debug -.->|"stdio"| tools
    fetch -->|"HTTPS, curl User-Agent"| statsguru
```

## Statsguru coverage (`query_stats`)
`query_stats` exposes Statsguru's own basic and advanced forms for every stat type, with Statsguru's field names and values (R4, R5). Nothing is renamed or regrouped.
- **Basic form:** Team, Opposition, Home or away, Host country, Ground, Starting date, Season, Match result, View format.
- **Advanced form:** the basic fields plus Continent, Series, Trophy, Tournament type, Match type, Day/night matches, Toss result, Batting or fielding first, Captaincy, Wicketkeeper, Debut or last match, Type of Batter, Age at start of match, Match involving players, Match involving captains, Innings in match, Runs scored in an inns, Batting position, Dismissed, Type of dismissal, Group figures by, Result qualifications, Sort results by, Results per page.
- **Result qualifications:** the form shows one; Statsguru also accepts a second and third (R2), and `query_stats` allows all three.
- **Other stat types:** the fields above are from the batting forms. The bowling, fielding, all-round, partnership, team and aggregate forms swap in their own fields and minimum and sort options (R4, R5).

## MCP tools (v1)
Five tools (D14), all read-only.
- **Answer tools** return `answer_markdown` plus structured data. The markdown has a short answer, a table, the method and assumptions, labelled pinned links, profile links for the players it names, the "as of" date and the freshness line.
- **Ambiguous names** (players, teams, grounds, trophies) return `needs_clarification` with the candidates.
- **Descriptions** start with example questions taken from the golden questions.

1. **`leaderboard`** (golden question 1): "Who has the best or fastest …?"
   - Parameters: format, metric, period, filters by name (team, opposition, host country, ground, trophy, home or away, match result), minimum and top N.
   - Metrics can be Statsguru columns (runs, average, strike rate, hundreds, …) or derived rates (innings per hundred, innings per fifty-plus, balls per dismissal).
   - For a derived rate it also gives the group's overall figure, for example total innings ÷ total hundreds across all qualifying players.
2. **`better_than_player`** (golden questions 2 and 3): "Who beats player X on A (and B)?"
   - Parameters: player name, format, 1–3 metrics, all or any, period (all time, X's career span, or dates), minimum and filters.
   - Includes X's own row. When Statsguru's extra minimums can express the comparison, the proof link shows the answer directly (D16).
3. **`player_record`** (golden questions 4 and 5): one player's figures in a format.
   - Parameters: player name, format, period (whole career, first or last N years of their career, dates or season) and filters (opposition, host country, ground, trophy such as the ODI World Cup, home or away, match result).
   - Proof link: the player's Statsguru page with the same filters; player pages accept dates and trophies (R6).
4. **`find_player`**: candidates with ID, country, formats and career spans (R7).
5. **`query_stats`**: any Statsguru query (see coverage above).
   - Returns up to `limit` rows (default 50, maximum 200), the total row count, exact columns and the pinned link.
   - With `fetch=false` it only builds the link, so no request is made.

Server instructions tell the agent to:
- prefer the answer tools and show `answer_markdown` as-is;
- not do multi-row arithmetic itself;
- cite only links that came from the tools;
- read "T20" as T20I unless a league is named (D3).

## Calculations (inside the answer tools)
- **Metric registry (batting first):** for each metric it records:
  - the label;
  - the Statsguru column, sort field and qualification field, if Statsguru has the metric;
  - the exact formula from totals, if it's derived;
  - which direction is better, and its default minimum, which is stated in answers.

  Examples: average = runs ÷ (innings − not outs); strike rate = runs ÷ balls × 100; innings per hundred = innings ÷ hundreds.
- **Exact columns** are recomputed from totals, and rankings and comparisons use them. Ties and borderline cases are flagged (D15; truncated display values in R2).
- **Period resolver:**
  - "X's career span" is X's first and last match start dates in that format, from X's innings list (R6).
  - "The last Y years of X's career" runs from Y years before X's last match to that match.

## Proof links and freshness
- **Pinned dates:** every link has `spanmin1` set to the format's first match date (from the built-in tables) and `spanmax1` set to today, unless the question's period is narrower (D16).
- **The link is the answer when possible:** answer tools add the final filter to the link through `qualval2` and `qualval3` where Statsguru can express it, and fetch the link once to confirm (D16).
- **Freshness line:** taken from the results page's list of current or recent matches (R8; D17).
- **Profile links:** each player named in an answer gets a link to their Cricinfo profile, built from their player ID without fetching it (D16; R6).

## Built-in IDs
- **Generator:** `scripts/gen_ids.py`, run only by developers, reads the advanced form pages for classes 1, 2, 3, 11 and 6. It writes a committed Python module with teams, host countries, continents, trophies (including World Cups and leagues) and each class's first match date (R4; D18). Re-run it by hand when a team or league is added.
- **On demand:** grounds and series are looked up on the form pages when a query needs them, and kept in memory.
- **Parameter meanings** live in code and are tested against synthetic form pages.

## Fetcher
Implements D7–D12 and D17.
- **Client:** httpx, sending curl's User-Agent string as a constant.
- **State per process, in memory:** the rate limiter, retry state and block pause.
- **Cache:** compressed, in memory, keyed by standard URL, with the settled, recent and lookup rules and the size cap.
- **Requests:** a page cap with a "too broad" message, and progress notifications while waiting.
- **Tests:** a test-only hook serves synthetic pages instead of the network.
- **Settings** (environment variables, or matching flags): `CRICKEY_MIN_INTERVAL`, `CRICKEY_MAX_RETRIES`, `CRICKEY_BLOCK_PAUSES`, `CRICKEY_MAX_PAGES`, `CRICKEY_CACHE_MAX_MB`, `CRICKEY_RECENT_TTL`, `CRICKEY_PORT` and `CRICKEY_IN_CONTAINER`.

## HTTP transport
- **Serving:** `crickey serve`, which plain `crickey` also runs, serves `mcp.streamable_http_app()` with uvicorn on port 8765 (`--port` or `CRICKEY_PORT`), as one process. This is the default mode (D20).
- **Binding:** natively it binds 127.0.0.1 and rejects other addresses. With `CRICKEY_IN_CONTAINER=1`, which the image sets, it binds 0.0.0.0 inside the container (D20).
- **Host/Origin checks:** an allowlist through `TransportSecuritySettings` with `127.0.0.1:*`, `localhost:*` and `[::1]:*` (R12). No CORS, no authentication.
- **Other:** responses stream as SSE so progress notifications arrive; `/health` returns only `{"status": "ok"}`; startup checks that the port is free.
- **stdio (debugging only):** `crickey stdio` runs the same server over stdio for the MCP Inspector (`mcp dev`) and tests. Each stdio session is a new process with an empty cache and its own request spacing and block pause, so the README doesn't offer it for everyday use (D20).

## Docker image
- **Image:** `ghcr.io/<you>/crickey` for `linux/amd64` and `linux/arm64`, tagged `<version>`, `<major>.<minor>` and `latest` (D24).
- **Dockerfile:** multi-stage.
  - The build stage uses uv to install crickey and its dependencies into a virtual environment from public PyPI, and precompiles bytecode. An optional BuildKit secret can override the package index for local builds (D23).
  - The final stage is R15's slim Python base image with only that environment, running as a non-root user.
  - `ENTRYPOINT ["crickey"]`, default command `serve`, `CRICKEY_IN_CONTAINER=1` and `PYTHONDONTWRITEBYTECODE=1`. No `VOLUME`.
  - OCI labels for the source repo, a description that includes the disclaimer, and the MIT licence.
  - A `.dockerignore` keeps tests, caches and research data out.
- **Read-only:** the container never writes to disk, so it runs with `--read-only` (D19).
- **Usage:**
  - HTTP (default): `docker run -d --rm --read-only --name crickey -p 127.0.0.1:8765:8765 ghcr.io/<you>/crickey:<version>`, then connect clients to `http://127.0.0.1:8765/mcp`. One container serves all of a person's clients and keeps its cache until it stops; after a reboot, run the command again.
  - stdio, for debugging only: `docker run -i --rm --read-only ghcr.io/<you>/crickey:<version> stdio`
  - Settings are passed as `-e CRICKEY_…` environment variables.

## CI pipeline (GitHub Actions)
- **`ci.yml`** (pull requests and pushes to `main`):
  - ruff and pytest on `ubuntu-latest` and `windows-latest`, using uv with R15's Python version and public PyPI;
  - build the image for `linux/amd64` without pushing, then run a smoke test with `--read-only`: start it in its default HTTP mode, check `/health`, list tools and call `query_stats` with `fetch=false` (no network needed) through `mcp.Client` over HTTP, and check that `stdio` still lists the tools.
- **`release.yml`** (tags `v*`):
  - rerun the tests;
  - set up QEMU and Buildx, and log in to GHCR with `GITHUB_TOKEN` (`packages: write`);
  - generate tags and labels with `docker/metadata-action`;
  - build and push both architectures with `docker/build-push-action`, with provenance and SBOM attestations;
  - smoke-test the pushed image by digest;
  - create a GitHub Release with notes and the `docker run` command.
- **Hygiene:** actions pinned to commit SHAs, least-privilege `permissions`, and Dependabot for actions, Python dependencies and the base image.
- **One-time step:** GHCR creates new packages as private, so after the first push the package is switched to public in its settings. The `org.opencontainers.image.source` label links it to the repo.

## README
The README covers:
- the disclaimer;
- the image name and tags;
- the `docker run` command, which starts the HTTP server published on loopback only, and that it must be running before clients connect (run it again after a reboot);
- one generic JSON snippet with the URL `http://127.0.0.1:8765/mcp`, and the Copilot CLI one-liner (`copilot mcp add --transport http --timeout 300000 crickey http://127.0.0.1:8765/mcp`);
- the environment variables;
- why requests are slow, that the cache resets when the container stops, and that one container should serve all your clients;
- one line on `stdio` for debugging.

## Todos (tracked in SQL)
1. **`project-setup`**: the git repo already exists with these docs.
   - Update local uv and install Python with it, at R15's versions.
   - Create a uv project with package `crickey`, dependencies at R15's versions, and the subcommands `serve` (the default) and `stdio` (for debugging).
   - Add an MIT LICENSE, a `.gitignore` (including `uv.lock`) and a README draft with the disclaimer. Don't commit an index URL (D23).
2. **`config-policy`**: settings from environment variables and flags only (no config file), with validation (D19).
3. **`polite-fetcher`**: the fetcher described above, tested with respx and a fake clock. It includes an opt-in live smoke test that httpx sending curl's User-Agent string still gets 200 (R1).
4. **`results-parsers`**: parsers for results tables (generic across stat types), the "current or recent matches" note, player pages (summary and innings list), player search and form pages (R6–R8). Includes column converters, exact batting columns and detection of HTML structure changes. Synthetic fixtures go in the repo; real pages stay local.
5. **`id-tables`**: the generator script, the committed tables, and on-demand lookups for grounds, series and the `player_involve`/`captain_involve` IDs, which come from the form's name search (R4).
6. **`query-spec-urls`**: the Pydantic query spec covering Statsguru's basic and advanced fields (R4) for all stat types and classes 1, 2, 3, 11 and 6, plus periods; and the standard-URL compiler with plain-English labels. Golden URL tests include a Babar Azam T20I example and an ODI World Cup (trophy) filter.
7. **`metric-registry`**: batting metric definitions, the name resolver, the period resolver (career span; first or last N years of a career), the proof-link builder and the answer renderer with the freshness line and profile links.
8. **`mcp-server-core`**: `MCPServer` with `find_player` and `query_stats`, server instructions, read-only annotations and progress notifications.
9. **`answer-tools`**: `leaderboard`, `better_than_player` and `player_record`, for batting.
10. **`http-transport`**: `crickey serve` and `crickey stdio` as described above. Tests for 421 (wrong Host), 403 (bad Origin), loopback-only binding natively, binding in container mode, and progress over SSE.
11. **`stat-type-coverage`**: `query_stats` builds valid URLs and parses tables for all 7 stat types and the 5 classes, with synthetic fixtures and one opt-in live query per type.
12. **`integration-tests`**: `mcp.Client` over HTTP and stdio using the synthetic-page hook, covering every tool and error path, plus a check that nothing is written to disk. Live tests are opt-in.
13. **`docker-image`**: the Dockerfile, `.dockerignore`, the build-secret index override, and a smoke-test script that runs with `--read-only`. Build it and run it locally in HTTP mode, and check stdio for debugging.
14. **`ci-pipeline`**: `ci.yml` and `release.yml` as described above.
15. **`readme`**: the README as described above.
16. **`sharing-release`**: make the repo public, tag `v0.1.0`, confirm both architectures are published, switch the GHCR package to public, and pull and run the image on a machine without the source.
17. **`copilot-cli-setup`**: `crickey serve`, then `copilot mcp add --transport http --timeout 300000 crickey http://127.0.0.1:8765/mcp`, checked with `/mcp`.
18. **`golden-questions`**: the five golden questions in decisions.md, with a strong and a small model.
    - Check the answers using the proof links, record them as regression tests, and run them once through the Docker image.
    - Any question the tools can't answer well is a candidate for the next tool.

**Dependencies:**
- setup → config → fetcher → {parsers, ID tables}
- {parsers, ID tables} → spec and URLs → metric registry → server core
- server core → {answer tools, HTTP transport, stat-type coverage}
- {answer tools, HTTP transport, stat-type coverage} → integration tests → Docker image → {CI pipeline, README} → sharing release
- HTTP transport → Copilot CLI setup
- {Copilot CLI setup, integration tests, Docker image} → golden questions

## Testing
- **Unit tests:**
  - URL compiler golden tests, spec validation, and parsers on synthetic fixtures (including the "current or recent matches" note).
  - Built-in ID tables: their format, lookups, and the fallback to form pages.
  - Exact columns and metric formulas, including ties and truncated display values.
  - Name resolution and clarification, and the period resolver (career span; first or last N years).
  - The fetcher: rate limiter, page cap, retries (`Retry-After`, jitter limits, time budget), pauses after a block, memory of failing URLs, the cache size cap and the freshness rules.
- **Answer tools:** each one against synthetic pages, with expected answers and proof links.
- **HTTP tests:** Host and Origin checks (localhost on any port), loopback-only binding natively, binding in container mode, and SSE progress.
- **End to end:** tests over HTTP and stdio using the synthetic-page hook, a check that no files are written, and the `--read-only` container smoke test. CI runs on Ubuntu and Windows. Live smoke tests run only when explicitly enabled.
- **Acceptance:** the five golden questions are answered correctly with both models, every pinned link shows matching numbers, and the public image works on a machine without the source.

## Risks and open items
- **Small tool set:** questions outside the three answer tools go through `query_stats`, which a weak model may struggle with. That's intended: each such question shows which tool to add next.
- **Built-in IDs go stale slowly:** new teams or leagues are missing until the generator script is re-run. The on-demand form lookup covers the gap.
- **Speed:** the 15 s spacing makes uncached multi-step answers slow. Mitigations: answer tools keep the number of requests low, plus caching, progress notifications and longer client timeouts.
- **No authentication:** any program running on the machine can call the HTTP server (web pages can't, because of the Host and Origin checks). In Docker, publishing the port beyond loopback would expose it to the network; the README only shows loopback publishing.

## Out of scope for v1
- **Data and formats:** women's and youth cricket; anything on www.cricinfo.com.
- **Access and distribution:** access from your network or the internet; non-Docker installs for friends (uvx, PyPI, MCP Bundles, plugins, install links); listing in MCP registries or directories; beginner Docker docs; auto-start at login.
- **Other:** prefetching or crawling; MCP Apps UI; packaging as an Agent Skill.
