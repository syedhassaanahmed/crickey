# Decisions

Key decisions for crickey. Each one says what was decided and why, and keeps its number for good: a new decision takes the next number and goes at the end, under "Added while building". Options considered and dropped are listed at the end, so they aren't reopened without new information.
- [plan.md](plan.md) has the goal and golden questions, puts these decisions into practice and refers to them by number (D1, D2, …).
- [research.md](research.md) has the supporting facts, referred to here as R1–R16.

## Product and scope
1. **An MCP server.** Not an Agent Skill, and not a standalone agent. Code, rather than instructions, controls how Cricinfo is accessed and how links are built, and any MCP client can use it. An agent can bypass a Skill's instructions, and Skills need a host that runs code.
2. **Build our own.** Paid tools are ruled out, and no free project covers filtered Statsguru queries (R11). `cricguru` (MIT) is useful as reference code.
3. **Formats.** Full Statsguru queries for men's Tests, ODIs, T20Is, all internationals combined and all Twenty20 (domestic and franchise leagues plus T20Is); R3 has their class numbers. "T20" means T20I unless a league is named. Women's and youth cricket are excluded.
4. **First-class and List A.** Statsguru's query engine doesn't serve them (R9). They can only be supported through Cricinfo's fixed record lists, and only once a real question needs them.
5. **Stat types.** Batting, bowling, fielding, all-round, partnerships, team and aggregates. No umpires or referees.

## Data access
6. **Personal use at each user's own risk.** Cricinfo's robots.txt disallows results pages (R1) and its terms ban data-extraction tools. Everyone who runs crickey accepts that risk. There's no consent step. Instead, the README and the image description carry a disclaimer: crickey is for personal use at the user's own risk, Cricinfo's terms ban data-extraction tools, and crickey isn't affiliated with Cricinfo, ESPNcricinfo or ESPN.
7. **Only stats.cricinfo.com is fetched.** www.cricinfo.com, including its player profile pages, blocks scripts (R1) and is never fetched. Nothing tries to get around a block. Answers can still link to it (D16).
8. **On demand only.** Requests happen only during tool calls: no prefetching, crawling or background refresh.
9. **15 seconds between requests** by default, matching robots.txt's crawl delay (R1). It can be lowered to 2 seconds, with a warning.
10. **At most 4 result pages (800 rows) per tool call.** Broader queries are refused with a "too broad" message. There's no daily request budget.
11. **Retries take seconds; pauses take minutes.**
    - Timeouts, server errors (5xx) and 429 are retried within the tool call up to 3 times, after about 15 s, 30 s and 60 s, honouring `Retry-After` and the call's time budget.
    - A block (403 or a challenge page) is never retried. The call fails at once, and later calls pause for 5 min → 15 min → 1 h → 4 h → 24 h, with one test request after each pause.
    - Restarting the server clears the pause.
12. **curl's User-Agent string.** Python's default User-Agents are refused and curl's is accepted (R1), so the code sends curl's string as a constant. It never imitates a browser, rotates identities or uses proxies.

## Answers and calculations
13. **The server does the work, so weaker models cope.** It fetches, calculates and formats the answer; the model picks a tool and passes plain parameters.
14. **Five tools in v1:** `leaderboard`, `better_than_player`, `player_record`, `find_player` and `query_stats`.
    - The three answer tools cover the golden questions and start with batting; plan.md maps each tool to its questions.
    - More tools are added only when real questions need them.
15. **Statsguru's numbers as displayed.** Averages, strike rates and economy rates are used as Statsguru displays them (R2, R8), not recomputed from totals, so answers show the same figures as Statsguru and its proof links (D16). Players whose displayed values are equal count as tied, and answers say so.
16. **Proof links.**
    - Links come only from the tools and carry a fixed date range, so shared links don't change.
    - When Statsguru can express the final filter (for example with its extra minimums, R2), the link itself shows the answer and is fetched once to confirm.
    - Otherwise the answer links the input table and shows the formula.
    - Answers also link each player they name to their Cricinfo profile, using the profile link Statsguru itself uses (R6). These links are for context; the proof is always the Statsguru link.
17. **Freshness.**
    - Past matches never change, so pages whose date range ended more than 7 days ago are cached without expiry.
    - New matches change aggregates, so pages covering the last 7 days are refreshed after an hour.
    - The in-memory cache is capped at 64 MB, dropping the least recently used pages first.
    - Answers name the newest match Statsguru included, and warn if a match may still be in progress.
18. **Built-in IDs.** Cricinfo's team IDs don't change, so teams, host countries, continents, trophies and leagues, and each format's first match date are built into the code. Grounds and series are looked up when needed.

## Runtime
19. **Stateless.** Everything lives in memory for the life of the process: no files written, no config file and no volumes. Settings come from environment variables and command-line flags. Separate instances don't share their cache or request spacing.
20. **HTTP by default; stdio only for debugging.**
    - Everyone runs Streamable HTTP on 127.0.0.1, started manually: natively, or in Docker with the port published only on the host's loopback address.
    - One long-running process per person serves all their clients, so the cache, request spacing and block pause carry across sessions.
    - stdio is kept only for debugging, because each stdio session starts a fresh process that forgets all three.
    - No authentication: binding to localhost plus the SDK's Host and Origin checks (R12) keeps web pages out.
21. **Stack.** Python with uv and the MCP Python SDK (`mcp[cli]`); httpx, cachetools, lxml, pydantic, pandas, rapidfuzz and uvicorn, with pytest, respx and ruff for development.
22. **Latest stable versions.** Use the latest stable releases of Python, uv, the SDK and libraries, the Docker base image and GitHub Actions; R15 lists them. Pre-releases aren't used.
23. **Package index.** Public PyPI by default. No index URL is committed, so each machine can point uv or pip at another index, including for local image builds. `uv.lock` isn't committed.

## Sharing
24. **Docker only.** Friends run a public image for amd64 and arm64 from GitHub Container Registry, in HTTP mode (D20). GitHub Actions builds, tests and publishes it for each version tag. The README stays short.
25. **Public repository, MIT licence.** Tests use synthetic pages; no Cricinfo content is committed.
26. **Clients.** You use Copilot CLI over HTTP. Friends use any MCP client that can connect to a Streamable HTTP server on localhost. Web chat apps aren't supported, because they need a hosted server (R14).

## Added while building
27. **Lower bounds for mcp and ruff.** Their lower bounds are one release below R15's versions, at 2.2.0 and 0.16.9, so crickey also installs from package indexes that don't have the newest release yet. mcp 2.2.0 already supports the 2026-07-28 spec (R15), and with no committed `uv.lock` (D23), uv still installs the newest release available (D22).
28. **Page cache storage from cachetools.** The page cache stores pages in `cachetools.TLRUCache` (R15) instead of its own LRU code; D17's freshness rules stay crickey's own.
29. **Rate comparisons use a runs floor when needed.** When a batting answer compares average or strike rate and the user does not give a minimum, the tool uses a runs minimum rather than an innings or balls-faced minimum. That matches the golden-question sample in R10 and avoids broad rate tables that exceed D10 before the comparison can be made.
30. **Dependabot tracks Python dependencies with the uv ecosystem.** crickey is a uv project and does not commit `uv.lock` (D23); Dependabot's uv updater supports `pyproject.toml` without a lockfile (R15), so it can update the manifest directly while staying aligned with the local and CI install path.

## Considered and dropped

| Option | Why it was dropped |
|---|---|
| A standalone agent with its own planning loop | Replaced by an MCP server that any agent can use. |
| Packaging as an Agent Skill | Can't control data access or link building, and depends on the host running code. |
| Building on Crawlora, CricketIQ-MultiAgent or Apify | Paid, or not usable as a base (R11). |
| Women's and youth formats; umpires and referees | Not needed. |
| Analysis through DuckDB SQL, then a Docker Python sandbox | Replaced by server-side calculations; no code is ever executed. |
| Letting the calling model do the maths | Too error-prone for weaker models. |
| Recomputing averages and strike rates exactly from totals | The owner accepts Statsguru's displayed values; exact values only differ below the last decimal shown (R2, R8). |
| More answer tools (`compare_players`, `player_breakdown`, `player_milestones`, `player_form`, `records`), a safe calculator, `compose_answer`, MCP resources and prompts, tool profiles | More than needed for now; added when real questions call for them. |
| stdio only, then stdio as the Docker default | Each stdio session is a fresh process, so the cache, request spacing and block pause reset every session. HTTP is the default everywhere; stdio is kept for debugging. |
| A bearer token (first required, then optional) | Removed; localhost plus Host and Origin checks is enough for now. |
| Access from other devices or the internet, OAuth and TLS | Not needed for personal use. |
| Starting automatically at login | Manual start is enough. |
| Starting the pause after a block at 15 minutes | Shortened to start at 5 minutes. |
| A daily request budget | Unnecessary with on-demand fetching and the per-call page cap. |
| An on-disk cache, a config file, a Docker volume, CSV files, and `unblock` and `catalog` commands | Dropped to keep the server stateless. |
| A fixed 24-hour cache expiry | Replaced by the freshness rules above. |
| A stored filter catalogue | Replaced by built-in ID tables plus lookups when needed. |
| Fetching player profile pages from www.cricinfo.com | Blocked to scripts (R1), so getting through would mean working around bot protection. Their statistics match Statsguru's, so answers link to them instead. |
| A separate access-check step before building | Done during research (R1, R9); the fetcher's live smoke test re-checks it. |
| uvx, PyPI, MCP Bundles, a Copilot plugin, one-click install links, MCP Registry or directory listings (R14) | Docker only. |
| A private container image | Public, so friends don't need to log in. |
| An MCP Apps UI | Not needed for v1. |
| A full HTTP cache library (hishel), and libraries for the fetcher's retries and spacing (tenacity, aiolimiter, pyrate-limiter, httpx-retries) | HTTP caches decide freshness from HTTP headers instead of the query's date range (D17), sit below the rate limiter, and hishel's storage does not fit D19 (R15). The fetcher's shared lock, per-call budgets, process-wide `Retry-After` and block pause (D11) are crickey-specific, and these libraries keep their own clocks. |
