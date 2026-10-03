# Decisions

Key decisions for crickey, as of 3 October 2026. Each one says what was decided and why. Options considered and dropped are listed at the end, so they aren't reopened without new information.
- [plan.md](plan.md) puts these into practice and refers to them as D1–D26.
- [research.md](research.md) has the supporting facts.

## Goal
- Answer cricket statistics questions using only Cricinfo's Statsguru as the data source.
- Every answer includes Cricinfo links that anyone can open to check the numbers and share as proof.
- These golden questions must work:
  1. Average number of innings taken per ODI century (minimum X number of centuries).
  2. Which players have scored Test hundreds more frequently than player X?
  3. Which batters had better average and strike rate in T20 than player X, in the same period that player X played?
  4. What was player X's Test batting average in the last Y years of his career?
  5. How many hundreds has player X scored in ODI World Cups?

## Product and scope
1. **An MCP server.** Not an Agent Skill, and not a standalone agent. Code, rather than instructions, controls how Cricinfo is accessed and how links are built, and any MCP client can use it. An agent can bypass a Skill's instructions, and Skills need a host that runs code.
2. **Build our own.** Paid tools are ruled out. Crawlora's open-source server only forwards calls to its paid API, CricketIQ-MultiAgent has no licence and falls back to made-up data, and the other projects don't query Statsguru. `cricguru` (MIT) is useful as reference code.
3. **Formats.** Full Statsguru queries for men's Tests (class 1), ODIs (2), T20Is (3), all internationals combined (11) and all men's Twenty20 (6: domestic and franchise leagues plus T20Is). "T20" means T20I unless a league is named. Women's and youth cricket are excluded.
4. **First-class and List A.** Statsguru's query engine doesn't serve them (it returns 503). They can only be supported as Cricinfo's fixed record lists, if those pages are reachable, and only once a real question needs them.
5. **Stat types.** Batting, bowling, fielding, all-round, partnerships, team and aggregates. No umpires or referees.

## Data access
6. **Personal use at each user's own risk.** Cricinfo's robots.txt disallows results pages and its terms ban data-extraction tools. Everyone who runs crickey accepts that risk. There's no consent step; the disclaimer is in the README and the image description.
7. **Only stats.cricinfo.com is fetched.** www.cricinfo.com, including its player profile pages, blocks scripts (403) and is never fetched. Nothing tries to get around a block. Answers can still link to it (D16).
8. **On demand only.** Requests happen only during tool calls: no prefetching, crawling or background refresh.
9. **15 seconds between requests** by default, matching robots.txt's crawl delay. It can be lowered to 2 seconds, with a warning.
10. **At most 4 result pages (800 rows) per tool call.** Broader queries are refused with a "too broad" message. There's no daily request budget.
11. **Retries take seconds; pauses take minutes.**
    - Timeouts, server errors (5xx) and 429 are retried within the tool call: about 15 s, then 30 s, at most 3 attempts, honouring `Retry-After`.
    - A block (403 or a challenge page) is never retried. The call fails at once, and later calls pause for 5 min → 15 min → 1 h → 4 h → 24 h, with one test request after each pause.
    - Restarting the server clears the pause.
12. **curl's User-Agent string.** Python's default User-Agents get 403; curl's and a custom `crickey/0.1` get 200. The code sends curl's string as a constant. It never imitates a browser, rotates identities or uses proxies.

## Answers and calculations
13. **The server does the work, so weaker models cope.** It fetches, calculates and formats the answer; the model picks a tool and passes plain parameters.
14. **Five tools in v1:** `leaderboard`, `better_than_player`, `player_record`, `find_player` and `query_stats`.
    - The three answer tools cover the golden questions (`player_record` was added for questions 4 and 5) and start with batting.
    - More tools are added only when real questions need them.
15. **Exact numbers.** Statsguru cuts off displayed decimals, so comparisons use values recomputed from totals, and ties and borderline cases are flagged.
16. **Proof links.**
    - Links come only from the tools and carry a fixed date range, so shared links don't change.
    - When Statsguru can express the final filter (for example with its extra minimums, `qualval2` and `qualval3`), the link itself shows the answer and is fetched once to confirm.
    - Otherwise the answer links the input table and shows the formula.
    - Answers also link each player they name to their Cricinfo profile, using the link Statsguru itself uses (`/ci/content/player/<id>.html`, which leads to the profile on www.cricinfo.com). These links are for context; the proof is always the Statsguru link.
17. **Freshness.**
    - Past matches never change, so pages whose date range ended more than 7 days ago are cached without expiry.
    - New matches change aggregates, so pages covering the last 7 days are refreshed after an hour.
    - The in-memory cache is capped at 64 MB, dropping the least recently used pages first.
    - Answers name the newest match Statsguru included, and warn if a match may still be in progress.
18. **Built-in IDs.** Cricinfo's team IDs don't change, so teams, host countries, continents, trophies and leagues, and each format's start date are built into the code (generated once by a developer script). Grounds and series are looked up when needed.

## Runtime
19. **Stateless.** Everything lives in memory for the life of the process: no files written, no config file and no volumes. Settings come from environment variables and command-line flags. Separate instances don't share their cache or request spacing.
20. **HTTP by default; stdio only for debugging.**
    - Everyone runs Streamable HTTP on 127.0.0.1: natively with `crickey serve` (started manually), or in Docker with the port published only on the host's loopback address.
    - One long-running process per person serves all their clients, so the cache, request spacing and block pause carry across sessions.
    - stdio (`crickey stdio`) is kept only for debugging, because each stdio session starts a fresh process that forgets all three.
    - No authentication: binding to localhost plus the SDK's Host and Origin checks keeps web pages out.
21. **Stack.** Python with uv and the MCP Python SDK (spec 2026-07-28); httpx, lxml, pydantic, pandas, rapidfuzz and uvicorn, with pytest, respx and ruff for development.
22. **Latest stable versions.** Use the latest stable releases of Python, uv, the SDK and libraries, the Docker base image and GitHub Actions; research.md lists them (R15). Pre-releases, such as Python 3.15 release candidates, aren't used.
23. **Package index.** Public PyPI by default. No index URL is committed, so each machine can point uv or pip at another index, including for local image builds (through a build secret). `uv.lock` isn't committed.

## Sharing
24. **Docker only.** Friends run a public image for amd64 and arm64 from GitHub Container Registry with `docker run -d --rm --read-only -p 127.0.0.1:8765:8765 …` and connect their clients to it. GitHub Actions builds, tests and publishes it for each version tag. The README stays short.
25. **Public repository, MIT licence.** Tests use synthetic pages; no Cricinfo content is committed.
26. **Clients.** You use Copilot CLI over HTTP. Friends use any MCP client that can connect to a Streamable HTTP server on localhost. Web chat apps aren't supported, because they only connect to hosted servers.

## Considered and dropped

| Option | Why it was dropped |
|---|---|
| A standalone agent with its own planning loop | Replaced by an MCP server that any agent can use. |
| Packaging as an Agent Skill | Can't control data access or link building, and depends on the host running code. |
| Building on Crawlora, CricketIQ-MultiAgent or Apify | Paid, a front for a paid API, or unlicensed with made-up fallback data. |
| Women's and youth formats; umpires and referees | Not needed. |
| Analysis through DuckDB SQL, then a Docker Python sandbox | Replaced by server-side calculations; no code is ever executed. |
| Letting the calling model do the maths | Too error-prone for weaker models. |
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
| Fetching player profile pages from www.cricinfo.com | Blocked to scripts (403), so getting through would mean working around bot protection. Their statistics match Statsguru's, so answers link to them instead. |
| A separate access-check step before building | Done during research (R1, R9); the fetcher's live smoke test re-checks it. |
| uvx, PyPI, MCP Bundles, a Copilot plugin, one-click install links, MCP Registry listing | Docker only. |
| A private container image | Public, so friends don't need to log in. |
