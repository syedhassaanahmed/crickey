# Decisions

The decisions behind crickey, as they stand now. Each says what holds and why, and keeps its number for good, because code, tests and docs cite it as D1, D2, …
- **Current state only.** When a decision changes, rewrite it in place, and its ADR if it has one. Don't keep the old version, dated notes or the story of the change; where an old result is the reason for a decision, state the reason once.
- **A new decision** takes the next number and goes at the end, under "Added while building".
- **ADRs:** a big or complex decision also has a record in [adr/](adr/), in [MADR 4.0.0](https://github.com/adr/madr/tree/4.0.0)'s format, named after its number: D43's is `adr/0043-anyone-can-host-their-own-crickey.md`. The ADR has the context, the options considered and why each was dropped; the entry here is a short summary that links to it. To add one, copy MADR's [template](https://github.com/adr/madr/blob/4.0.0/template/adr-template.md) to `adr/<number, 4 digits>-<title>.md`, keep the sections that help, and link it from the entry.
- **Options considered and dropped** that no ADR covers are listed at the end, so they aren't reopened without new information.
- [plan.md](plan.md) has the goal and puts these decisions into practice, [research.md](research.md) has the supporting facts, as R1–R17, and [evals/golden_questions.toml](../evals/golden_questions.toml) lists the golden questions (D41).

## Product and scope
1. **An MCP server.** Not an Agent Skill, and not a standalone agent. Code, rather than instructions, controls how Cricinfo is accessed and how links are built, and any MCP client can use it. An agent can bypass a Skill's instructions, and Skills need a host that runs code.
2. **Build our own.** Paid tools are ruled out, and no free project covers filtered Statsguru queries (R11). `cricguru` (MIT) is useful as reference code.
3. **Formats.** Full Statsguru queries for men's Tests, ODIs, T20Is, all internationals combined and all Twenty20 (domestic and franchise leagues plus T20Is); R3 has their class numbers. "T20" means T20I unless a league is named. Women's and youth cricket are excluded.
4. **First-class and List A.** Statsguru's query engine doesn't serve them (R9). They can only be supported through Cricinfo's fixed record lists, and only once a real question needs them.
5. **Stat types.** Batting, bowling, fielding, all-round, partnerships, team and aggregates. No umpires or referees.

## Data access
6. **Personal use at each user's own risk.** Cricinfo's robots.txt disallows results pages (R1) and its terms ban data-extraction tools. Everyone who runs crickey accepts that risk. There's no consent step. Instead, the README carries the disclaimer: crickey is for personal use at the user's own risk, Cricinfo's terms ban data-extraction tools, and crickey isn't affiliated with Cricinfo, ESPNcricinfo or ESPN. The image description says what crickey is.
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
14. **Six tools in v1:** `leaderboard`, `better_than_player`, `player_record`, `series_count`, `find_player` and `query_stats`.
    - The four answer tools cover the golden questions, and the three about players start with batting; the golden questions file says which tool answers each (D41).
    - More tools are added only when real questions need them, as `series_count` was (D44).
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
18. **Built-in IDs.** Cricinfo's team IDs don't change, so teams, host countries, continents, trophies and leagues, and each format's first match date are built into the code. Grounds are looked up when a filter names one, and series names are read when `series_count` lists series.

## Runtime
19. **Stateless.** Everything lives in memory for the life of the process: no files written, no config file and no volumes. Settings come from environment variables and command-line flags. Separate instances don't share their cache or request spacing.
20. **HTTP by default; stdio only for debugging.** Everyone runs one long-running Streamable HTTP server on 127.0.0.1, natively or in Docker published on the host's loopback, so the cache, request spacing and block pause carry across sessions and clients. stdio is for debugging. There's no authentication: localhost plus the SDK's Host and Origin checks (R12) keeps web pages out. [ADR 0020](adr/0020-http-by-default-stdio-for-debugging.md)
21. **Stack.** Python with uv and the MCP Python SDK (`mcp[cli]`); httpx, cachetools, lxml, pydantic, pandas, rapidfuzz and uvicorn, with pytest, respx and ruff for development.
22. **Latest stable versions.** Use the latest stable releases of Python, uv, the SDK and libraries, the Docker base image and GitHub Actions; R15 lists them. Pre-releases aren't used.
23. **Package index.** Public PyPI by default. No index URL is committed, so each machine can point uv or pip at another index, including for local image builds. `uv.lock` isn't committed.

## Sharing
24. **Docker only.** Friends run a public image for amd64 and arm64 from GitHub Container Registry, in HTTP mode (D20). GitHub Actions builds, tests and publishes it for each version tag. The README stays short.
25. **Public repository, MIT licence.** Tests use synthetic pages; no Cricinfo content is committed.
26. **Clients.** You use Copilot CLI over HTTP. Friends use any MCP client that can connect to a Streamable HTTP server on localhost. Web chat apps aren't supported, because they need a hosted server (R14).

## Added while building
27. **Lower bounds for mcp and ruff.** Their lower bounds are one release below R15's versions, at 2.2.0 and 0.17.0, so crickey also installs from package indexes that don't have the newest release yet. mcp 2.2.0 already supports the 2026-07-28 spec (R15), ruff 0.17.0 already supports Python 3.15 as a target (R15), and with no committed `uv.lock` (D23), uv still installs the newest release available (D22).
28. **Page cache storage from cachetools.** The page cache stores pages in `cachetools.TLRUCache` (R15) instead of its own LRU code; D17's freshness rules stay crickey's own.
29. **Batting rate comparisons use a runs floor when needed.** With no minimum from the user, a comparison of average or strike rate uses 1,000 runs, or 1,500 for combined internationals, never above the compared batter's own runs, so the table fits D10 and the batter qualifies. [ADR 0029](adr/0029-batting-rate-comparisons-use-a-runs-floor.md)
30. **Dependabot tracks Python dependencies with the uv ecosystem.** crickey is a uv project and does not commit `uv.lock` (D23); Dependabot's uv updater supports `pyproject.toml` without a lockfile (R15), so it can update the manifest directly while staying aligned with the local and CI install path.
31. **The image smoke test is Bash over the wire.** The owner asked for a Bash script instead of Python. The image jobs use curl and jq, so they do not need Python dependencies or uv after the image is built, and the test checks the raw Streamable HTTP wire protocol the way a third-party client would. The script has no pytest tests of its own; CI runs it against the built image, and the release against the pushed one.
32. **Bowling rates use a wickets floor when needed.** With no minimum from the user, ranking or comparing bowling average, economy rate or bowling strike rate uses a wickets floor for the format, lower under any narrowing filter or period, so the table fits D10. [ADR 0032](adr/0032-bowling-rates-use-a-wickets-floor.md)
33. **Count leaderboards have no default floor.** A leaderboard of a count, such as wickets or catches, uses a minimum of 1 unless the user gives one, because a higher floor can only hide results. [ADR 0033](adr/0033-count-leaderboards-have-no-default-floor.md)
34. **`player_record` splits a record instead of a `player_breakdown` tool.** `player_record` takes an optional `split_by` rather than a new `player_breakdown` tool, because the player's summary page, which it already reads, has the grouped rows (R6). So a split needs no extra request or new tool (D14). Continents are kept apart from host countries because the page groups them separately (R6).
35. **Fielding answers use one set of floors.** `leaderboard` and `better_than_player` rank and compare six fielding metrics, with one set of floors for all of them, lower under any narrowing filter. `player_record` stays batting and bowling. [ADR 0035](adr/0035-fielding-answers-use-one-set-of-floors.md)
36. **"All formats" means all internationals combined.** A question across all formats, such as golden question 7, uses the combined Test, ODI and T20I class (R3), the only class in D3 that spans those formats. All Twenty20 is one format that adds domestic and franchise leagues, so "all formats" doesn't include it, just as "T20" means T20I unless a league is named (D3).
37. **Evals ask live Statsguru, about answers that can't change.** The eval suite (`evals/`, Inspect AI) asks the golden questions through models against live Statsguru, within D7–D12's limits, on demand with local models rather than in CI, and scores the answers with deterministic checks. Every case's answer can't change, and logs are never committed. [ADR 0037](adr/0037-evals-ask-live-statsguru.md)
38. **Tool results report what they cost Statsguru in `_meta`.** Every tool result's `_meta`, errors included, has `crickey/statsguru`: the Statsguru requests the call sent, retries included, and the pages it read from the cache. The eval's cost check needs them per question, and `_meta` keeps them out of what models read (R17). Each tool call starts a fresh tally, because an MCP server can run its requests in one shared context. A tool's error becomes an error result with MCPServer's own text plus the tally, since MCPServer's error results carry no `_meta`.
39. **The eval's packages are an optional dependency group, left out of the image.** `inspect-ai` and `openai` are in the `evals` group, which the eval's commands add with `uv run --group evals`. crickey's own `uv sync`, its tests and CI don't install it, so they don't depend on those packages being on the package index, and the Dockerfile's `uv sync --no-default-groups` leaves every group out of the image. As in D27, their lower bounds, inspect-ai 0.3.275 and openai 3.23.0, are older than R15's, so they install from package indexes that don't have the newest releases yet.
40. **The eval reaches crickey through its own MCP bridge.** Inspect's MCP tools would cut crickey's tool schemas down to what Inspect's parameter model keeps, losing everything under `$defs`, and they drop `_meta` (R17). So the eval lists crickey's tools itself, inlines the `$ref`s into keywords Inspect keeps, and calls each tool over a fresh Streamable HTTP session, recording the call's `_meta` (D38). When crickey reports a block, the bridge stops the run instead of handing the model an error (D11).
41. **The golden questions are listed once, with the evals.** [evals/golden_questions.toml](../evals/golden_questions.toml) is the one place they're listed. Other files refer to a golden question by its number, and crickey's code doesn't read the file. [ADR 0041](adr/0041-golden-questions-listed-once-with-the-evals.md)
42. **Periods are plain text.** The answer tools take `period` as a string in one of the forms plan.md lists, such as "last 3 years" or "2000 to 2009", and reject anything else before any request. [ADR 0042](adr/0042-periods-are-plain-text.md)
43. **Anyone can host their own crickey in the cloud.** `CRICKEY_HOST`, `CRICKEY_URL` and `CRICKEY_TOKEN` let a user run their own instance on a cloud platform. crickey won't start without a token once other machines can reach it, and TLS is the platform's. [ADR 0043](adr/0043-anyone-can-host-their-own-crickey.md)
44. **Series questions have their own answer tool.** `series_count` counts series from Statsguru's series view, with plain parameters and one request, so that small models can answer them. [ADR 0044](adr/0044-series-questions-have-their-own-answer-tool.md)
45. **Tool descriptions never quote an eval case, and held-out cases check each change.** Descriptions and the server instructions share no wording, players or values with any eval case. Each golden question has held-out cases, and a change made for a failing case is checked on them too. [ADR 0045](adr/0045-tool-descriptions-never-quote-an-eval-case.md)

## Considered and dropped
Options that no ADR covers; each ADR lists its own.

| Option | Why it was dropped |
|---|---|
| A standalone agent with its own planning loop | An MCP server works with any agent (D1). |
| Packaging as an Agent Skill | Can't control data access or link building, and depends on the host running code. |
| Building on Crawlora, CricketIQ-MultiAgent or Apify | Paid, or not usable as a base (R11). |
| Women's and youth formats; umpires and referees | Not needed. |
| Analysis through DuckDB SQL or a Docker Python sandbox | The server calculates answers itself and never executes code (D13). |
| Letting the calling model do the maths | Too error-prone for weaker models. |
| Recomputing averages and strike rates exactly from totals | The owner accepts Statsguru's displayed values; exact values only differ below the last decimal shown (R2, R8). |
| More answer tools (`compare_players`, `player_milestones`, `player_form`, `records`), a safe calculator, `compose_answer`, MCP resources and prompts, tool profiles | More than needed for now; added when real questions call for them (D14). |
| A first pause of 15 minutes after a block | The pauses start at 5 minutes (D11). |
| A daily request budget | Unnecessary with on-demand fetching and the per-call page cap. |
| An on-disk cache, a config file, a Docker volume, CSV files, and `unblock` and `catalog` commands | The server keeps no state outside its process (D19). |
| A fixed 24-hour cache expiry | Settled pages never change, and recent ones are refreshed hourly (D17). |
| A stored filter catalogue | Built-in ID tables, with lookups when needed, cover the filters (D18). |
| Fetching player profile pages from www.cricinfo.com | Blocked to scripts (R1), so getting through would mean working around bot protection. Their statistics match Statsguru's, so answers link to them instead. |
| Linking or fetching the new Statsguru on www.cricinfo.com/statsguru | crickey can't fetch or confirm it (D7, R1), the classic engine serves the same data, and repeated keys cover multi-value queries (R2). |
| A separate access-check step | Research checked access (R1, R9), and the live test re-checks it. |
| uvx, PyPI, MCP Bundles, a Copilot plugin, one-click install links, MCP Registry or directory listings (R14) | Docker only (D24). |
| A private container image | Public, so friends don't need to log in. |
| Running CI on `windows-latest` | crickey ships as a Linux image (D24), which Ubuntu CI and the image job cover. |
| An MCP Apps UI | Not needed for v1. |
| A full HTTP cache library (hishel), and libraries for the fetcher's retries and spacing (tenacity, aiolimiter, pyrate-limiter, httpx-retries) | HTTP caches decide freshness from HTTP headers instead of the query's date range (D17), sit below the rate limiter, and hishel's storage does not fit D19 (R15). The fetcher's shared lock, per-call budgets, process-wide `Retry-After` and block pause (D11) are crickey-specific, and these libraries keep their own clocks. |
| Inspect's own MCP tools for the eval | They drop crickey's `$defs` and `_meta` (D40). |
| An 8-bit KV cache for local models | It reads prompts about 4.6 times slower on a CPU (R17). |
| Asking golden question 8 "in how many countries" | It tells the model what to count; the answer check reads the count in more forms instead. |
