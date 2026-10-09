# Decisions

Key decisions for crickey. Each one says what was decided and why, and keeps its number for good: a new decision takes the next number and goes at the end, under "Added while building". Options considered and dropped are listed at the end, so they aren't reopened without new information.
- [plan.md](plan.md) has the goal and golden questions, puts these decisions into practice and refers to them by number (D1, D2, …).
- [research.md](research.md) has the supporting facts, referred to here as R1–R17.

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
29. **Rate comparisons use a runs floor when needed.** When a batting answer compares average or strike rate and the user does not give a minimum, the tool uses a runs minimum rather than an innings or balls-faced minimum. That matches the golden-question sample in R10 and avoids broad rate tables that exceed D10 before the comparison can be made. In `better_than_player`, the floor never exceeds the target player's value for the floor field under the same filters, so the player being compared always qualifies. The tool reads that value only when the player is missing from the qualifying rows, so comparisons where the player already qualifies, such as golden questions 2 and 3, make no extra request. The player page offers no team filter (R6), so under a team filter it gives the player's figure across all their teams: exact for a player with one team, but only an upper bound for a player with more than one, who can still be missing; the tool then asks for an explicit minimum.
30. **Dependabot tracks Python dependencies with the uv ecosystem.** crickey is a uv project and does not commit `uv.lock` (D23); Dependabot's uv updater supports `pyproject.toml` without a lockfile (R15), so it can update the manifest directly while staying aligned with the local and CI install path.
31. **The image smoke test is Bash over the wire.** The owner asked for a Bash script instead of Python. The image jobs use curl and jq, so they do not need Python dependencies or uv after the image is built, and the test checks the raw Streamable HTTP wire protocol the way a third-party client would. The script has no pytest tests of its own; CI runs it against the built image, and the release against the pushed one.
32. **Bowling rate comparisons use a wickets floor when needed.** When a bowling answer compares bowling average, economy rate or bowling strike rate and the user does not give a minimum, the tool uses a wickets minimum. Unfiltered all-time questions use Tests 100, ODIs 100, T20Is 50, all T20 100 and combined internationals 200. Questions with any narrowing filter (continent, host, opposition, ground, trophy, team, home or away, match result, or a period other than all-time) use lower floors: Tests and ODIs 30, T20Is 20, all T20 50 and combined internationals 50. In `better_than_player`, the floor never exceeds the target player's value for the floor field under the same filters, so the player being compared always qualifies, except possibly a player with more than one team under a team filter (D29). This matches D29's reason for batting rate comparisons: it avoids broad rate tables that exceed D10 before the comparison can be made, and it uses the rate's natural denominator. R10 has the measured row counts for the unfiltered and filtered floors.
33. **Count leaderboards have no default floor.** When `leaderboard` ranks a count metric, one whose default minimum is on the ranked field itself (runs, hundreds, fifties, wickets, five-wicket hauls and ten-wicket matches), and the user does not give a minimum, it uses a minimum of 1 on that field and states it in the answer. Statsguru sorts the whole table and the leaderboard reads only page 1, so a higher floor can only hide results: in the v0.1.1 retest, Test wickets in Asia for England or South Africa came back empty under `wickets >= 100` (R10 has the table with a minimum of 1). Rate and derived metrics keep their floors from the metric registry and D32.
34. **`player_record` splits a record instead of a `player_breakdown` tool.** `player_record` takes an optional `split_by` rather than a new `player_breakdown` tool, because the player's summary page, which it already reads, has the grouped rows (R6). So a split needs no extra request, and D14 stays at five tools. Continents are kept apart from host countries because the page groups them separately (R6).
35. **Fielding answers use one set of floors.** `leaderboard` and `better_than_player` take `discipline=fielding`, with six Statsguru columns as metrics: catches, catches as a fielder, catches as a wicketkeeper, stumpings, dismissals and dismissals per innings (R5, R8). Each count's floor is on its own field, and dismissals per innings uses a dismissals floor, the count of successes, as D29 uses runs and D32 wickets. Unfiltered all-time questions use Tests 50, ODIs 50, T20Is 25, all T20 100 and combined internationals 100. Questions with any narrowing filter (D32's list, including a period other than all-time) use Tests 20, ODIs 20, T20Is 20, all T20 50 and combined internationals 30. One set of numbers covers every fielding metric because no fielding count exceeds dismissals, and R10 has the measured row counts, all within D10 for a 10-year span. Count leaderboards keep D33's minimum of 1. In `better_than_player`, the floor never exceeds the target player's own figure (D29); as for batting, that figure is read only when the player is missing, so comparisons where the player qualifies cost no extra request. Answers say that Statsguru's fielding figures count catches and stumpings but not run-outs, and when catches or dismissals include those taken as a wicketkeeper. `player_record` stays batting and bowling: D34's splits count hundreds or five-wicket hauls, and fielding has no equivalent yet.
36. **"All formats" means all internationals combined.** A question across all formats, such as golden question 7, uses the combined Test, ODI and T20I class (R3), the only class in D3 that spans those formats. All Twenty20 is one format that adds domestic and franchise leagues, so "all formats" doesn't include it, just as "T20" means T20I unless a league is named (D3).
37. **Evals ask live Statsguru, about answers that can't change.** The owner's choices (8 Oct 2026, #18):
    - The eval suite (`evals/`, Inspect AI, R17) asks the golden questions through language models against live Statsguru everywhere, locally and in CI, within D7–D12's limits. A block stops the run.
    - CI spends no AI credits, so its models run in Ollama on GitHub's Linux runners (#65).
    - Every case's answer can't change: the players have retired, and comparisons with other players use a period that has ended, such as the player's career span.
    - Scores come from deterministic checks, with no LLM judge, which would be weak with a local model.
    - Logs hold Statsguru's figures, so they're never committed (D25); CI keeps them with each run.
38. **Tool results report what they cost Statsguru in `_meta`.** Every tool result's `_meta` has `crickey/statsguru`: the Statsguru requests the call sent, retries included, and the pages it read from the cache. The eval's cost check needs them per question, and `_meta` keeps them out of what models read (R17). Each tool call starts a fresh tally, because an MCP server can run its requests in one shared context.
39. **The eval's packages are a default dependency group, left out of the image.** `inspect-ai` and `openai` are in the `evals` group. `uv sync` installs it with `dev` (`default-groups`), so the eval's tests run with the rest, and the Dockerfile's `uv sync --no-default-groups` leaves both groups out of the image. As in D27, their lower bounds are the releases the owner's package index had on 9 Oct 2026, below R15's: inspect-ai 0.3.275 and openai 3.23.0.
40. **The eval reaches crickey through its own MCP bridge.** Inspect's MCP tools would cut crickey's tool schemas down to what Inspect's parameter model keeps, losing everything under `$defs`, and they drop `_meta` (R17). So the eval lists crickey's tools itself, inlines the `$ref`s into keywords Inspect keeps, and calls each tool over a fresh Streamable HTTP session, recording the call's `_meta` (D38). When crickey reports a block, the bridge stops the run instead of handing the model an error (D11).

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
| More answer tools (`compare_players`, `player_milestones`, `player_form`, `records`), a safe calculator, `compose_answer`, MCP resources and prompts, tool profiles | More than needed for now; added when real questions call for them. |
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
| Linking or fetching the new Statsguru on www.cricinfo.com/statsguru | crickey can't fetch or confirm it (D7, R1), the classic engine serves the same data, and repeated keys cover multi-value queries (R2). |
| A separate access-check step before building | Done during research (R1, R9); the fetcher's live smoke test re-checks it. |
| uvx, PyPI, MCP Bundles, a Copilot plugin, one-click install links, MCP Registry or directory listings (R14) | Docker only. |
| A private container image | Public, so friends don't need to log in. |
| Running CI on `windows-latest` | The owner runs crickey only in Linux containers, which Ubuntu CI and the image job cover. |
| An MCP Apps UI | Not needed for v1. |
| A full HTTP cache library (hishel), and libraries for the fetcher's retries and spacing (tenacity, aiolimiter, pyrate-limiter, httpx-retries) | HTTP caches decide freshness from HTTP headers instead of the query's date range (D17), sit below the rate limiter, and hishel's storage does not fit D19 (R15). The fetcher's shared lock, per-call budgets, process-wide `Retry-After` and block pause (D11) are crickey-specific, and these libraries keep their own clocks. |
| pydantic-evals, DeepEval or promptfoo for the evals | Inspect AI has the agent, MCP tools, model providers and log viewer. pydantic-evals would need those built, DeepEval's MCP metrics need an LLM judge and its dashboards are hosted, and promptfoo is Node.js (R17). |
| GitHub Models or Copilot for evals in CI | GitHub Models was retired on 30 Jul 2026, and Copilot spends AI credits (R17). |
| Synthetic pages for evals | The owner wants evals to ask live Statsguru (D37); tests stay synthetic (D25). |
| Inspect's own MCP tools for the eval | They drop crickey's `$defs` and `_meta` (D40). |
| An 8-bit KV cache for local models | It reads prompts about 4.6 times slower on a CPU (R17). |
