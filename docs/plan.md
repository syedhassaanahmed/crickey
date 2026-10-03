# Plan: `crickey`, an MCP server for Cricinfo Statsguru

## Problem and approach
Build an MCP server that answers cricket stats questions using only Cricinfo, with Cricinfo links in every answer so people can check the numbers and share them. It covers Statsguru's basic and advanced filters.

Each person runs their own copy on their own machine:
- **You** run it natively, over Streamable HTTP on localhost or over stdio.
- **Friends** run a public Docker image from GitHub Container Registry (GHCR). A CI pipeline builds, tests and publishes it. The friends are technical and know Docker, so the docs stay short.

**The server is stateless.** Everything lives in memory for the life of the process. There are no volumes, no config file and no disk writes, and the container runs with a read-only filesystem.

**Start small.** v1 has four tools: two answer tools that cover your three example questions, a player lookup, and a general Statsguru query. More tools are added only when real questions need them.

The server does the fetching, calculating and answer formatting, so weaker models only need to pick a tool and pass plain parameters.

The workspace `C:\projects\crickey` is empty, so this is a new project.

## Confirmed decisions
| Topic | Decision |
|---|---|
| Data access | Personal use at each user's own risk. Cricinfo's robots.txt blocks `template=results` pages and its terms ban data-extraction tools; the README and the image description say so. No consent step. Fetch only on demand, cache in memory, and pause when blocked. |
| State | **None on disk.** The cache, rate limiter, pauses and looked-up filter values are kept in memory and disappear when the process exits. Settings come from environment variables and command-line flags only. |
| User-Agent | **curl's default User-Agent string** (`curl/<version>`), a constant in code. Cricinfo refuses Python library User-Agents with 403 but accepts curl's. This presents the client as curl to get past that filter, which is your decision. No browser impersonation, no rotation and no proxies; if curl's string is blocked too, crickey pauses and stops. |
| Freshness | Match data never changes, but a new match changes aggregates. Pages whose date range ended more than 7 days ago are cached with no expiry. Pages reaching the last 7 days (including the default "up to today") are reused for at most 1 hour (configurable). Each answer names the newest match Statsguru included and warns if one may still be in progress. |
| Formats | **Full Statsguru queries** (all basic and advanced filters): men's Tests (`class=1`), ODIs (`2`), T20Is (`3`), all internationals combined (`11`), and all men's Twenty20 (`6`: domestic and franchise leagues plus T20Is). Women's and youth cricket are excluded. "T20" defaults to T20I unless the question names a league. First-class (`4`) and List A (`5`) exist only as fixed record lists; their access is checked early, and a tool for them is added when a real question needs it. |
| Stat types | `query_stats` handles batting, bowling, fielding, all-round, partnerships, team and aggregates. The answer tools start with batting and gain other types as real questions need them. No umpires or referees. |
| Tools (v1) | `leaderboard`, `better_than_player`, `find_player` and `query_stats`. No calculator, no answer-composition tool, no resources, prompts or profiles. |
| IDs | **Built into the code:** team IDs (international and domestic or franchise), host countries, continents, leagues (trophies) and each format's first match date. These are generated once by a developer script and committed. Grounds and series are looked up on demand from Statsguru's form pages, and so are names missing from the built-in tables. |
| Stack | Python with uv and the MCP Python SDK v2.3 (`MCPServer`, 2026-07-28 spec). |
| Package index | Public PyPI by default (uv's built-in default). Nothing in the repo sets an index URL, so any machine can switch to another index with its own uv or pip settings (`UV_DEFAULT_INDEX`, a user-level `uv.toml`, or `PIP_INDEX_URL`). `uv.lock` isn't committed, because it records the index it was resolved from. Local image builds can override the index through a BuildKit secret, so it never ends up in an image layer. |
| Transport | Natively: Streamable HTTP on `127.0.0.1` (`crickey serve`) or stdio (`crickey stdio`). In Docker: stdio by default; HTTP optional, published only on the host's loopback. No authentication: loopback binding plus the SDK's Host/Origin checks. |
| Request spacing | 15 s by default (Cricinfo's robots.txt `Crawl-delay`), enforced within each process. Can be lowered to 2 s, with a warning. Short retries (seconds) for temporary errors; a growing pause between tool calls after a block (5 min → 15 min → 1 h → 4 h → 24 h), which lasts for the life of the process. |
| Sharing | **Docker only.** A public multi-architecture image (`linux/amd64`, `linux/arm64`) at `ghcr.io/<you>/crickey`, built, tested and published by GitHub Actions for every version tag. The source repo is public under MIT. No PyPI, uvx, bundles, plugins or registry listings. |
| Clients | You: Copilot CLI over HTTP (native). Friends: any MCP client that can launch a local command. Web chat apps aren't supported, because they only connect to hosted servers. |

## Architecture
```
You:     Copilot CLI ── HTTP ──► http://127.0.0.1:8765/mcp   (native, uv; one long-running process)
Friends: MCP client  ── stdio ─► docker run -i --rm --read-only ghcr.io/<you>/crickey:<version>   (one container per client session)
                                   │
                                   ▼
crickey MCP server (Python, MCPServer): one copy per person, on their own machine
  ├─ Tools: leaderboard · better_than_player · find_player · query_stats
  ├─ Shared logic: metric registry · name resolver · period resolver · proof-link builder · answer renderer
  ├─ Query spec + built-in ID tables (+ on-demand form lookups) ─► URL compiler (standard form, pinned "as of" date)
  ├─ Polite fetcher: allowlist · 15 s spacing · retries · pause when blocked · in-memory cache · page cap
  └─ Parsers ─► tables (pandas, exact columns)
All state is in memory and lasts as long as the process.
```

## Statsguru facts confirmed during research
- **Host and URLs:** `stats.cricinfo.com` (`stats.espncricinfo.com` redirects there). Cricinfo rewrites every query into one standard URL form: alphabetical parameters separated by `;`. Results pages use `template=results` and accept exact date ranges (`spanmin1`/`spanmax1`/`spanval1=span`).
- **Hidden extra minimums work:** `qualval2`/`qualval3` (with their min/max values) are accepted, even though the form shows only one. Page size can be 10–200 rows, and results show "Page X of Y".
- **Cut-off decimals:** displayed averages and strike rates are truncated, not rounded (4188 ÷ 86 = 48.698 shows as 48.69).
- **Player search:** `analysis.html?search=…;template=analysis` returns IDs, countries and per-format spans. Player pages offer 24 views, and their forms also accept date ranges.
- **All T20 (`class=6`):** the filter form works and lists domestic and franchise teams. League records exist for IPL, BBL, PSL, CPL, SA20, the Blast and others.
- **First-class and List A:** the Statsguru query engine returns **503** for `class=4` and `class=5`. The Records section lists fixed first-class and List A lists at `/ci/content/records/<id>.html`. A test fetch of one of those pages also returned 503, so access is **unconfirmed**.
- **www.cricinfo.com:** both www.cricinfo.com and www.espncricinfo.com return 403 to scripts, which is bot protection. They're not used, and nothing tries to get around this.
- **robots.txt:** blocks `template=results` and sets a 15 s crawl delay. It allows the filter forms, player search and records index.
- **User-Agent (tested 3 Oct 2026):** Python library User-Agents get **403** (`Python-urllib/3.14`, `python-httpx/0.28.1`). curl's default and a custom `crickey/0.1` get 200 on the same results page.
- **Freshness note on results pages:** each results page says "Statsguru includes the following current or recent <format> matches", followed by match names, dates and links. For example, Test batting listed "England v Pakistan at Birmingham, 3rd Test, Sep 9-12, 2026 [Test # 2635]".
- Pages cached during research (`research/sg_pages.json` in the session folder) help with development. They are not committed to the repo.

## Coverage (`query_stats`)
| Scope | What's supported |
|---|---|
| Basic filters | team, opposition, host country, ground, home/away/neutral, dates or season, match result, view (overall/innings/match/series/ground/host/opposition/year/season) |
| Advanced filters | continent, series, trophy (leagues), tournament type, finals, day/night, toss, batting/fielding first, captain, keeper, debut/last match, batter's hand, age, players or captains involved, innings number, runs-in-innings range, batting position, out/not out, dismissal type, group by (14 options), up to 3 result minimums, sort and reverse, page size |
| Stat types | batting, bowling, fielding, all-round, partnerships, team and aggregates, each with its own filters, minimums and sort fields; tables are parsed generically |

## MCP tools (v1)
Each tool returns `answer_markdown` plus structured data. The markdown contains:
- a short answer and a table;
- the method and assumptions;
- labelled Cricinfo links pinned to an "as of" date.

Inputs are plain names and enums, and every optional parameter has a default.

1. **`leaderboard`**: "Who has the best or fastest …?" Batting first.
   - Parameters: format, metric, period, filters by name (team, opposition, host, ground, league, home/away, result), minimum and top N.
   - Metrics can be native Statsguru columns (runs, average, strike rate, hundreds, …) or derived rates (innings per hundred, innings per fifty-plus, balls per dismissal).
   - For a derived rate it also gives the group's overall figure (for example, total innings ÷ total hundreds across all qualifying players).
   - *Example question 1.*
2. **`better_than_player`**: "Who beats player X on A (and B)?" Batting first.
   - Parameters: player name, format, 1–3 metrics, all/any, period (all time, X's career span, or dates), minimum and filters.
   - Includes X's own row. When Statsguru's extra minimums can express the comparison, it builds a proof link and checks it once.
   - *Example questions 2 and 3.*
3. **`find_player`**: candidates with ID, country, formats and career spans. The other tools use it internally; it's also exposed so the agent can resolve ambiguous names.
4. **`query_stats`**: the general tool for everything else. It runs any Statsguru query with every filter, up to 3 minimums, and any stat type, view or grouping.
   - Returns up to `limit` rows (default 50, maximum 200), the total row count, exact columns and the pinned link.
   - With `fetch=false` it only builds the link, which costs no request.

All tools are marked read-only. Server instructions tell the agent to:
- prefer the answer tools and show `answer_markdown` as-is;
- not do multi-row arithmetic itself;
- only cite links that came from the tools.

**Deferred until real questions need them:** compare_players, player_breakdown, player_milestones, player_form, records (first-class and List A), player_stats, filter_options, a calculator, answer composition, resources and prompts.

## Designed for weaker models
- **Few tools, clear jobs:** two answer tools return finished answers, so no maths or multi-step planning is needed for the common questions.
- **Easy inputs:** flat parameters, enums, names instead of IDs, and documented defaults (default minimums are stated in each answer).
- **No guessing:** ambiguous names (players, teams, grounds) return a `needs_clarification` result listing the candidates. Unsupported requests return an explicit message.
- **Descriptions that steer:** each tool description starts with example questions.
- **Tested on both:** the golden questions are run with a strong model and with a small one.

## Calculations (inside the answer tools)
- **Metric registry (batting for v1):** for each metric it records:
  - the label;
  - the Statsguru column, sort and minimum fields, if the metric is native;
  - the exact formula from totals, if it's derived;
  - which direction is better, and its default minimum.
  
  Examples: average = runs ÷ (innings − not outs); strike rate = runs ÷ balls × 100; innings per hundred = innings ÷ hundreds.
- **Exact values:** Statsguru's displayed decimals are cut off, so tables get exact columns recomputed from totals. Rankings and comparisons use them, and ties and boundary cases are flagged.
- **Period resolver:** "X's career span" means X's first and last match start dates in that format, taken from X's innings list.

## Proofs and citations
- **Pinned dates:** every link gets a pinned date range (`spanmin1` is the format's first match, `spanmax1` is the "as of" date, which is today), so shared links don't change later.
- **The link is the answer when possible:** answer tools put the final filter into the link where Statsguru can express it (for example `qualval2=batting_average;qualval3=batting_strike_rate` with X's values), so the linked table is the answer. That link is fetched once to confirm it. For derived metrics, the answer links the input table and shows the formula and calculated column.
- **Sources section:** every answer lists its labelled links, the "as of" date, the filters in plain English, and the minimums used.
- **Freshness line:** every answer names the newest match Statsguru included, taken from the page's "current or recent matches" note. If a listed match's dates reach today, the answer warns that it may still be in progress, so the linked numbers can change when it finishes.

## IDs and filter values
- **Built-in tables:** team IDs for every supported format (international plus domestic and franchise teams in `class=6`), host countries, continents, leagues (trophies) and each format's first match date. Cricinfo doesn't change these, so they live in code.
- **Generator script:** a developer-only script (`scripts/gen_ids.py`) reads the filter form pages for formats 1, 2, 3, 11 and 6 (a handful of polite requests) and writes the tables as a committed Python module. It's re-run by hand when a new team or league appears.
- **On demand:** grounds and series (large, growing lists), and any name missing from the built-in tables, are looked up on the relevant form page when a query needs them, then kept in memory.
- **Parameter meanings live in code:** field names, value types and allowed combinations are code, checked by tests against synthetic form pages.

## HTTP transport (localhost only)
- **Serving:** `crickey serve` serves `mcp.streamable_http_app()` with uvicorn on port 8765 (`--port` or `CRICKEY_PORT`), as one process. Natively it binds `127.0.0.1` and rejects any other address.
- **In Docker:** the image sets `CRICKEY_IN_CONTAINER=1`, so `serve` binds `0.0.0.0` inside the container. The README publishes it only on the host's loopback (`-p 127.0.0.1:8765:8765`); the container can't enforce that, so the README says not to publish it any other way.
- **DNS-rebinding protection:** only Host `127.0.0.1`, `localhost` or `[::1]` is accepted, on any port (so a different host port still works), and any `Origin` sent must match. No CORS.
- **No authentication:** no token or OAuth. Web pages in your browser can't reach the server because of the Host/Origin checks; programs running on your own PC can, which is acceptable for personal use.
- **Progress over SSE:** responses stream as SSE so progress notifications during waits reach the client.
- **Health route:** `/health` returns only `{"status": "ok"}`.
- **Startup check:** the port is free.
- **stdio:** `crickey stdio` runs the same server for clients that launch it as a subprocess or container.
- **Long-running is better:** one long-running HTTP server keeps its in-memory cache warm across all your client sessions. Each stdio process or container starts cold.

## Fetching rules
- **Allowlist:** only `stats.cricinfo.com`. www.cricinfo.com is never fetched, and nothing tries to get around blocks.
- **On demand only:** requests happen only during tool calls. No prefetching, crawling or background refresh.
- **User-Agent:** curl's default User-Agent string, set as a constant in code. It's never changed at runtime.
- **Spacing:** at least 15 s between requests within a process (configurable, minimum 2 s, with a warning below 15 s). Separate processes or containers don't coordinate, so the README suggests running one instance at a time.
- **Page cap:** at most 4 result pages per tool call (800 rows) by default. A broader query stops there and returns "too broad, add a minimum or filter" instead of spending minutes paging through results.
- **Retries (seconds, within one tool call):**
  - Timeouts, connection errors, 500/502/503/504 and 429 are retried after about 15 s, then 30 s, with jitter (at most 3 attempts), and only while the tool call's time budget allows.
  - `Retry-After` is honoured. It's kept as a "not before" time; if it's longer than the call's time budget, the call fails at once with "try again after HH:MM".
  - After a 429 or 503, spacing is temporarily raised and then eases back.
  - A URL that keeps failing the same way (for example, unsupported pages returning 503) is remembered as unavailable and not retried.
- **Pause after a block (minutes to hours, between tool calls):**
  - A 403 or bot-challenge page means you're blocked, and it's never retried. That tool call fails at once with "paused until HH:MM"; cached answers keep working during the pause.
  - When the pause ends, the next tool call that needs Cricinfo makes one test request (never in the background). If it's still blocked, the pause grows: 5 min → 15 min → 1 h → 4 h → 24 h. A successful request resets it.
  - Why so long: blocks are deliberate and rarely clear in seconds, requests made while blocked can extend them, they usually cover your whole IP (so also your normal browsing of Cricinfo), and AI agents often retry failed tool calls in a loop.
  - The durations are configurable. The User-Agent and IP are never changed. Restarting the process clears the pause.
- **Cache:**
  - In memory, compressed and keyed by standard URL, with a size cap (default 64 MB, least recently used pages dropped first).
  - **Settled pages, no expiry:** pages whose date range ended more than 7 days ago. Past matches never change, and 7 days covers a 5-day Test plus Statsguru's update delay.
  - **Recent pages, 1 hour (`CRICKEY_RECENT_TTL`):** pages whose range reaches the last 7 days, which includes the default "up to today". A new or finishing match can change these aggregates, so they're refetched after an hour. Within an hour, follow-up questions stay fast.
  - **Lookup pages:** player search and form pages stay cached for the life of the process. A name that isn't found triggers one refetch, in case it's a new player or series.

## Docker image
- **Image:** `ghcr.io/<you>/crickey`, public, for `linux/amd64` and `linux/arm64`. Tags `<version>`, `<major>.<minor>` and `latest` all point to the same digest.
- **Dockerfile:** multi-stage.
  - The build stage uses uv to install crickey and its dependencies into a virtual environment from public PyPI, and precompiles bytecode. An optional BuildKit secret can override the package index for local builds; it never ends up in an image layer.
  - The final stage is Python slim (the same minor version as development) with only that environment, running as a non-root user.
  - `ENTRYPOINT ["crickey"]`, default command `stdio`, `CRICKEY_IN_CONTAINER=1`, `PYTHONDONTWRITEBYTECODE=1`. No `VOLUME`.
  - OCI labels for the source repo, a description that includes the disclaimer, and the MIT licence.
  - A `.dockerignore` keeps tests, caches and research data out.
- **No mounts needed:** the container never writes to disk, so it runs with `--read-only`; CI enforces this.
- **Usage:**
  - **stdio, for MCP clients:** `docker run -i --rm --read-only ghcr.io/<you>/crickey:<version>`
  - **HTTP:** `docker run -d --rm --read-only --name crickey -p 127.0.0.1:8765:8765 ghcr.io/<you>/crickey:<version> serve`, then connect to `http://127.0.0.1:8765/mcp`. A long-running container keeps its cache warm across client sessions.
  - **Settings:** passed as `-e CRICKEY_…` environment variables.

## CI pipeline (GitHub Actions)
- **`ci.yml` (pull requests and pushes to main):**
  - ruff and pytest on Ubuntu and Windows (your development platform), with dependencies from public PyPI;
  - build the image for `linux/amd64` without pushing, and run a container smoke test with `--read-only`. It lists tools through `mcp.Client` over stdio, calls `query_stats` with `fetch=false` (no network needed), and checks HTTP mode responds on `/health`.
- **`release.yml` (tags `v*`):**
  - rerun the tests;
  - set up QEMU and Buildx, and log in to GHCR with `GITHUB_TOKEN` (`packages: write`);
  - generate tags and labels with `docker/metadata-action`;
  - build and push both architectures with `docker/build-push-action`, with provenance and SBOM attestations;
  - smoke-test the pushed image by digest;
  - create a GitHub Release with notes and the `docker run` command.
- **Hygiene:** actions pinned to commit SHAs, least-privilege `permissions`, and Dependabot for actions and the base image.
- **One-time step:** GHCR creates new packages as private, so after the first push the package is switched to public in its settings. The `org.opencontainers.image.source` label links it to the repo.

## README for friends (short)
The friends are technical and know Docker, so the README covers only:
- the disclaimer;
- the image name and tags;
- the stdio `docker run` command;
- one generic `mcpServers` JSON snippet and the Copilot CLI one-liner (`copilot mcp add crickey --timeout 300000 -- docker run -i --rm --read-only ghcr.io/<you>/crickey:<version>`);
- optional HTTP mode, plus the tip that a long-running container keeps the cache warm;
- the environment variables;
- why requests are slow, and that state resets when the container stops.

There are no Docker basics or per-client walkthroughs.

## Todos (tracked in SQL)
1. **`project-setup`**: git and a uv project with package `crickey`.
   - Dependencies, capped at the next major version: mcp[cli]≥2.3, httpx, lxml, pydantic, pandas, rapidfuzz, uvicorn. Dev tools: pytest, respx, ruff.
   - Subcommands: `serve` and `stdio`.
   - An MIT LICENSE, a `.gitignore` (including `uv.lock`), a README draft with the disclaimer, and no index URL in any committed file.
2. **`config-policy`**: settings from environment variables and command-line flags only (no config file), with validation. Settings: spacing, retries, block-pause durations, page cap, cache size, recent-page TTL (default 1 hour), port and container mode.
3. **`polite-fetcher`**: allowlist, curl's User-Agent string, in-memory rate limiter, compressed size-capped cache (settled pages kept, recent pages for 1 hour, lookup pages refetched on a miss), page cap, retries with jitter and `Retry-After`, adaptive slowdown, a growing pause after a block (5 min → 24 h, one test request per pause), memory of permanently failing URLs, and progress callbacks. Includes a test-only hook that serves synthetic pages instead of the network. Tested with respx and a fake clock.
4. **`access-check`**: with a handful of polite requests, check two things:
   - Does httpx sending curl's User-Agent string get 200? It may still differ from real curl in other ways. If it's refused, stop and report; never switch to a browser-like one.
   - Can first-class and List A record pages be fetched? Record the result for later.
5. **`results-parsers`**: parsers for results tables (generic across stat types), the "current or recent matches" note (names, dates, match IDs), player pages (summary and innings list), player search and form pages. Column converters, exact batting columns and detection of HTML structure changes. Synthetic fixtures in the repo; real ones kept locally.
6. **`id-tables`**: the generator script and the committed tables (teams for formats 1, 2, 3, 11 and 6, host countries, continents, leagues, format start dates), plus on-demand form lookups for grounds, series and unknown names. Work out the remaining unknown parameters.
7. **`query-spec-urls`**: the Pydantic query spec (all filters, all stat types, format 6 and periods) and the standard-URL compiler with labels. Golden URL tests, including the Kohli T20I example and a league (`trophy`) filter.
8. **`metric-registry`**: batting metric definitions (native and derived, exact formulas, which direction is better, default minimums), the name resolver (fuzzy matching plus `needs_clarification`), the period resolver, the proof-link builder and the answer renderer (including the freshness line).
9. **`mcp-server-core`**: `MCPServer` with `find_player` and `query_stats` (including `fetch=false`), instructions, read-only annotations and progress notifications.
10. **`answer-tools`**: `leaderboard` (including the group's overall figure for derived rates) and `better_than_player`, for batting.
11. **`http-transport`**: `crickey serve` (loopback natively, `0.0.0.0` in container mode), Host/Origin checks for localhost on any port, SSE progress, `/health`, the port check and `crickey stdio`. No authentication. Tests for 421, 403, loopback-only binding natively, container-mode binding and progress.
12. **`stat-type-coverage`**: make sure `query_stats` builds valid URLs and parses tables for all 7 stat types and 5 formats, with synthetic fixtures and one opt-in live smoke query per type.
13. **`integration-tests`**: `mcp.Client` over HTTP and stdio, using the synthetic-page hook, covering every tool and error path, plus a check that nothing is written to disk. Live tests are opt-in.
14. **`docker-image`**: the Dockerfile, `.dockerignore`, the BuildKit-secret index override, and a smoke-test script that runs with `--read-only`. Build and run it locally over stdio and HTTP.
15. **`ci-pipeline`**: `ci.yml` and `release.yml` as described, with pinned actions, least-privilege permissions and Dependabot.
16. **`readme-docker`**: the short README for technical friends.
17. **`sharing-release`**: make the repo public, tag `v0.1.0`, confirm both architectures are published, switch the GHCR package to public, and pull and run the image from a machine without the source.
18. **`copilot-cli-setup`**: `crickey serve`, then `copilot mcp add --transport http --timeout 300000 crickey http://127.0.0.1:8765/mcp`, checked with `/mcp`.
19. **`golden-questions`**: your 3 questions with a strong and a small model. Check the answers using the proof links, record them as regression tests, and run them once through the Docker image. Note any question the four tools can't answer well, as a candidate for the next tool.

**Dependencies:**
- setup → config → fetcher → {access check, parsers}
- {access check, parsers} → ID tables → spec/URLs
- {spec, parsers} → metric registry → server core
- server core → {answer tools, HTTP transport, stat-type coverage}
- {answer tools, HTTP, stat-type coverage} → integration tests
- integration tests → Docker image → {CI pipeline, README}
- {CI pipeline, README} → sharing release
- HTTP → Copilot CLI setup
- {Copilot CLI setup, integration tests, Docker image} → golden questions

## Testing
- **Unit tests:**
  - URL compiler golden tests, spec validation, and parsers on synthetic fixtures (including the "current or recent matches" note).
  - Built-in ID tables (format, lookups, fallback to the form page).
  - Exact columns and metric formulas, including ties and cut-off decimals.
  - Name resolution and clarification, and the period resolver.
  - Rate limiter, page cap, retries (`Retry-After`, jitter limits, time budget), growing pauses after a block, memory of failing URLs, the cache size cap and the cache rules (settled pages kept, recent pages expire after the TTL, a lookup miss refetches once), and the freshness line including the in-progress warning.
- **Answer tools:** `leaderboard` and `better_than_player` against synthetic pages, with expected answers and proof links.
- **HTTP tests:** Host/Origin checks (localhost on any port), loopback-only binding natively, container-mode binding, and SSE progress.
- **End to end:** tests over HTTP and stdio using the synthetic-page hook, a check that no files are written, and the `--read-only` container smoke test in CI. CI runs on Ubuntu and Windows. Live smoke tests only when explicitly opted in.
- **Acceptance:** the three example questions answered correctly with both models, every pinned link shows matching numbers, and the public image works from a machine without the source.

## Risks and open items
- **Terms and robots.txt:** each user carries their own risk; the README and image description carry the disclaimer. A public image makes the tool visible, and Cricinfo may block it. If that happens, respect it and don't evade.
- **curl User-Agent:** this presents the client as curl to get past Cricinfo's filter on Python clients, which you chose knowingly. Cricinfo may block it, and httpx may differ from real curl in other ways that a filter can see; the access check verifies it works. If it's blocked, crickey pauses and stops. An honest custom name (for example `crickey/0.1`) also got 200 and is the fallback if you change your mind.
- **Freshness:** a recent page can be up to an hour old, and a shared link pinned to today can still change if a match in its range was in progress. The freshness line flags both cases.
- **Cost of being stateless:**
  - Each new process or container starts with an empty cache, so its first answers are slower and need more Cricinfo requests.
  - Pauses reset on restart, so a blocked user who restarts makes one early test request.
  - Concurrent instances don't coordinate spacing.
  - Mitigation: a long-running HTTP server (native or container) keeps everything warm.
- **Small tool set:** questions outside the two answer tools go through `query_stats`, so a weak model may struggle with them. That's intended: each such question shows which tool to add next.
- **Built-in IDs go stale slowly:** new teams or leagues are missing until the generator script is re-run. The on-demand form lookup covers the gap.
- **Speed:** the 15 s spacing makes uncached multi-step answers slow. Mitigations: answer tools keep the number of requests low, plus caching, progress notifications and longer client timeouts.
- **To confirm while building:** parameters for players and captains involved, per-type filters, `qualmin` precision, the page parameter, and that player pages accept pinned date ranges.
- **No authentication:** any program running on the machine can call the HTTP server (web pages can't, because of the Host/Origin checks). In Docker HTTP mode, publishing the port beyond loopback would expose it to the network; the README only shows loopback publishing.
- **Block durations are a guess:** Cricinfo doesn't publish its blocking rules and we haven't been blocked on stats.cricinfo.com yet. The pause durations are configurable and should be tuned if real blocks are seen.
- **Docker details:** GHCR packages start private (a one-time switch to public). arm64 builds run under QEMU in CI and are slower, but all dependencies have arm64 wheels.
- **Reproducibility:** without a committed lockfile, each release resolves current dependency versions within the caps. The published image digest is the reproducible artifact.
- **Other:** Python 3.14 wheels (fall back to 3.13 for both development and the image), HTML changes, the HTTP server must be running (stdio is the fallback), and port conflicts.

## Out of scope for v1
- **Data and formats:** women's and youth cricket; anything on www.cricinfo.com.
- **Deferred tools:** compare_players, player_breakdown, player_milestones, player_form, records (first-class and List A), player_stats, filter_options, a calculator, answer composition, resources, prompts and tool profiles.
- **Behaviour and safety:** running free-form code; persistent state (disk cache, config file, volumes); a daily request budget; consent prompts; browser impersonation, User-Agent rotation or proxies; any authentication (bearer token or OAuth).
- **Access and distribution:** access from your network or the internet; non-Docker installs for friends (uvx, PyPI, MCP Bundles, plugins, install links); listing in MCP registries or directories; beginner Docker docs; auto-start at login.
- **Other:** prefetching or crawling; MCP Apps UI; packaging as an Agent Skill.
