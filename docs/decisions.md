# Decisions

Key decisions for crickey. Each one says what was decided and why, and keeps its number for good: a new decision takes the next number and goes at the end, under "Added while building". Options considered and dropped are listed at the end, so they aren't reopened without new information.
- [plan.md](plan.md) has the goal, puts these decisions into practice and refers to them by number (D1, D2, …).
- [evals/golden_questions.toml](../evals/golden_questions.toml) lists the golden questions (D41).
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
18. **Built-in IDs.** Cricinfo's team IDs don't change, so teams, host countries, continents, trophies and leagues, and each format's first match date are built into the code. Grounds and series are looked up when needed.

## Runtime
19. **Stateless.** Everything lives in memory for the life of the process: no files written, no config file and no volumes. Settings come from environment variables and command-line flags. Separate instances don't share their cache or request spacing.
20. **HTTP by default; stdio only for debugging.**
    - Everyone runs Streamable HTTP on 127.0.0.1, started manually: natively, or in Docker with the port published only on the host's loopback address.
    - One long-running process per person serves all their clients, so the cache, request spacing and block pause carry across sessions.
    - stdio is kept only for debugging, because each stdio session starts a fresh process that forgets all three.
    - No authentication: binding to localhost plus the SDK's Host and Origin checks (R12) keeps web pages out. An instance other machines can reach needs a token (D43).
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
29. **Rate comparisons use a runs floor when needed.** When a batting answer compares average or strike rate and the user does not give a minimum, the tool uses a runs minimum rather than an innings or balls-faced minimum. That matches the golden-question sample in R10 and avoids broad rate tables that exceed D10 before the comparison can be made. In `better_than_player`, the floor never exceeds the target player's value for the floor field under the same filters, so the player being compared always qualifies. The tool reads that value only when the player is missing from the qualifying rows, so comparisons where the player already qualifies, such as golden questions 2 and 3, make no extra request. The player page offers no team filter (R6), so under a team filter it gives the player's figure across all their teams: exact for a player with one team, but only an upper bound for a player with more than one, who can still be missing; the tool then asks for an explicit minimum.
30. **Dependabot tracks Python dependencies with the uv ecosystem.** crickey is a uv project and does not commit `uv.lock` (D23); Dependabot's uv updater supports `pyproject.toml` without a lockfile (R15), so it can update the manifest directly while staying aligned with the local and CI install path.
31. **The image smoke test is Bash over the wire.** The owner asked for a Bash script instead of Python. The image jobs use curl and jq, so they do not need Python dependencies or uv after the image is built, and the test checks the raw Streamable HTTP wire protocol the way a third-party client would. The script has no pytest tests of its own; CI runs it against the built image, and the release against the pushed one.
32. **Bowling rate comparisons use a wickets floor when needed.** When a bowling answer compares bowling average, economy rate or bowling strike rate and the user does not give a minimum, the tool uses a wickets minimum. Unfiltered all-time questions use Tests 100, ODIs 100, T20Is 50, all T20 100 and combined internationals 200. Questions with any narrowing filter (continent, host, opposition, ground, trophy, team, home or away, match result, or a period other than all-time) use lower floors: Tests and ODIs 30, T20Is 20, all T20 50 and combined internationals 50. In `better_than_player`, the floor never exceeds the target player's value for the floor field under the same filters, so the player being compared always qualifies, except possibly a player with more than one team under a team filter (D29). This matches D29's reason for batting rate comparisons: it avoids broad rate tables that exceed D10 before the comparison can be made, and it uses the rate's natural denominator. R10 has the measured row counts for the unfiltered and filtered floors.
33. **Count leaderboards have no default floor.** When `leaderboard` ranks a count metric, one whose default minimum is on the ranked field itself (runs, hundreds, fifties, wickets, five-wicket hauls and ten-wicket matches), and the user does not give a minimum, it uses a minimum of 1 on that field and states it in the answer. Statsguru sorts the whole table and the leaderboard reads only page 1, so a higher floor can only hide results: in the v0.1.1 retest, Test wickets in Asia for England or South Africa came back empty under `wickets >= 100` (R10 has the table with a minimum of 1). Rate and derived metrics keep their floors from the metric registry and D32.
34. **`player_record` splits a record instead of a `player_breakdown` tool.** `player_record` takes an optional `split_by` rather than a new `player_breakdown` tool, because the player's summary page, which it already reads, has the grouped rows (R6). So a split needs no extra request or new tool (D14). Continents are kept apart from host countries because the page groups them separately (R6).
35. **Fielding answers use one set of floors.** `leaderboard` and `better_than_player` take `discipline=fielding`, with six Statsguru columns as metrics: catches, catches as a fielder, catches as a wicketkeeper, stumpings, dismissals and dismissals per innings (R5, R8). Each count's floor is on its own field, and dismissals per innings uses a dismissals floor, the count of successes, as D29 uses runs and D32 wickets. Unfiltered all-time questions use Tests 50, ODIs 50, T20Is 25, all T20 100 and combined internationals 100. Questions with any narrowing filter (D32's list, including a period other than all-time) use Tests 20, ODIs 20, T20Is 20, all T20 50 and combined internationals 30. One set of numbers covers every fielding metric because no fielding count exceeds dismissals, and R10 has the measured row counts, all within D10 for a 10-year span. Count leaderboards keep D33's minimum of 1. In `better_than_player`, the floor never exceeds the target player's own figure (D29); as for batting, that figure is read only when the player is missing, so comparisons where the player qualifies cost no extra request. Answers say that Statsguru's fielding figures count catches and stumpings but not run-outs, and when catches or dismissals include those taken as a wicketkeeper. `player_record` stays batting and bowling: D34's splits count hundreds or five-wicket hauls, and fielding has no equivalent yet.
36. **"All formats" means all internationals combined.** A question across all formats, such as golden question 7, uses the combined Test, ODI and T20I class (R3), the only class in D3 that spans those formats. All Twenty20 is one format that adds domestic and franchise leagues, so "all formats" doesn't include it, just as "T20" means T20I unless a league is named (D3).
37. **Evals ask live Statsguru, about answers that can't change.** The owner's choices (8 Oct 2026, #18):
    - The eval suite (`evals/`, Inspect AI, R17) asks the golden questions through language models against live Statsguru, within D7–D12's limits. A block stops the run.
    - It runs on demand with local models, not in CI (29 Oct 2026, #65).
    - Every case's answer can't change: the players have retired, and comparisons with other players use a period that has ended, such as the player's career span.
    - Scores come from deterministic checks, with no LLM judge, which would be weak with a local model.
    - Logs hold Statsguru's figures, so they're never committed (D25).
38. **Tool results report what they cost Statsguru in `_meta`.** Every tool result's `_meta`, errors included, has `crickey/statsguru`: the Statsguru requests the call sent, retries included, and the pages it read from the cache. The eval's cost check needs them per question, and `_meta` keeps them out of what models read (R17). Each tool call starts a fresh tally, because an MCP server can run its requests in one shared context. A tool's error becomes an error result with MCPServer's own text plus the tally, since MCPServer's error results carry no `_meta`.
39. **The eval's packages are an optional dependency group, left out of the image.** `inspect-ai` and `openai` are in the `evals` group, which the eval's commands add with `uv run --group evals`. crickey's own `uv sync`, its tests and CI don't install it, so they don't depend on those packages being on the package index, and the Dockerfile's `uv sync --no-default-groups` leaves every group out of the image. As in D27, their lower bounds are the releases the owner's package index had on 9 Oct 2026, below R15's: inspect-ai 0.3.275 and openai 3.23.0.
40. **The eval reaches crickey through its own MCP bridge.** Inspect's MCP tools would cut crickey's tool schemas down to what Inspect's parameter model keeps, losing everything under `$defs`, and they drop `_meta` (R17). So the eval lists crickey's tools itself, inlines the `$ref`s into keywords Inspect keeps, and calls each tool over a fresh Streamable HTTP session, recording the call's `_meta` (D38). When crickey reports a block, the bridge stops the run instead of handing the model an error (D11).
41. **The golden questions are listed once, with the evals.** The owner's choice (18 Oct 2026, PR #67): [evals/golden_questions.toml](../evals/golden_questions.toml) is the one place they're listed. Each has its general form, a number, and its eval case (D37). Other files refer to a golden question by its number instead of quoting it, so changing one means editing only that file.
    - The file belongs to the evals, so crickey's code doesn't read it, and the image doesn't ship it.
    - The answer tools' descriptions keep example questions written for models, which never quote an eval case (D45). The evals measure whether they're enough, and failures become issues (D14).
42. **Periods are plain text.** The owner's choice (28 Oct 2026, #78): the answer tools take `period` as a string, such as "last 3 years", "2000 to 2009", "2000-01-01 to 2009-12-31" or "season 2011/12", instead of an object with a `kind` and its fields. In Ollama, the tool renderers of some models drop the object's kinds, and several models wrote periods as strings anyway (R17). A string is the plain parameter D13 asks for, and every renderer can show one. crickey reads only the forms plan.md lists, and rejects anything else before any request, with a message that lists them. A bare year is the calendar year; a season needs the word "season" or the 2011/12 form. The object form stopped working, which the owner accepted while crickey has few users.
43. **Anyone can host their own crickey in the cloud.** The owner's choice (29 Oct 2026, #40): crickey stays for personal use (D6), but a user who wants it on every device can run their own instance on a cloud platform. Settings make that possible, and leave D20's local defaults as they were:
    - `CRICKEY_HOST` (`--host`) sets the bind address.
    - `CRICKEY_URL` (`--url`) names the instance's public URLs, which the Host and Origin checks then accept as well as localhost; they would answer a cloud hostname with 421. One setting covers both checks, because without CORS no other web page can call crickey anyway.
    - With `CRICKEY_TOKEN` set, every request but `/health` needs `Authorization: Bearer <token>`. The token comes only from the environment, so it stays out of process lists, and needs at least 32 characters. crickey refuses to start without one when another machine could reach it: a bind address other than loopback, or a URL other than localhost. The image's 0.0.0.0 doesn't count, because Docker publishes it on the host's loopback (D20). `/health` stays open for the platform's health checks.
    - TLS is the hosting platform's.

    A hosted instance is still one person's: their devices share its request spacing and block pause (D9, D11), and Cricinfo may challenge a datacenter IP more often than a home one (R1).
44. **Series questions have their own answer tool.** The owner's choice (29 Oct 2026, #97): `series_count` answers golden question 10, rather than `query_stats`, so that small models can answer it too (D13). It takes plain parameters and makes one request to Statsguru's series view, whose total is the count and whose link is the proof (R4, D16).
    - `number_of_teams` takes "2", "3+", "3-4" or "5+", or the same in words, such as "three or more". It's text rather than an enum, so a model can write it its own way, and the description lists the values, since descriptions reach every model (R17). Statsguru can't tell three teams from four (R4), so "3" alone is an error that offers "3+" and "3-4".
    - Series count as Statsguru counts them: a series counts when any of its matches meets the filters, so a co-hosted tournament counts for each host country, and `team` counts the series a team played in.
    - The view shows no series names, so the tool reads them from the advanced form's series list, as lookups do (D18): one more request, the first time. The answer links only its proof, because models given each series' page cited those links instead (R17).
45. **Tool descriptions never quote an eval case, and held-out cases check each change.** The owner's choice (29 Oct 2026, #99): every golden question was in a tool description, three with the eval's own players, so a model could pass by matching the question to an example, and each fix had been checked only on the question it was made for (R17).
    - Tool descriptions, parameter descriptions and the server instructions never quote a golden question or an eval case, held-out or not: no run of 5 or more words in common, and none of an eval case's players or values. Their examples show the same kinds of question about other players, formats, places and periods.
    - Each golden question has at least one held-out case in [evals/golden_questions.toml](../evals/golden_questions.toml): the same kind of question in other words, about other players, teams, formats or periods, with an answer that can't change (D37). The eval reports held-out cases apart from the golden ones.
    - A change made for a failing case is checked on the held-out cases too, and never copies a case's wording, players or values.

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
| A test that fails when a description quotes an eval case (#99) | The owner's call: not needed. |
| Answering series questions through `query_stats` (#97) | Models struggle to write its queries, even with its fields named (R17); `series_count` takes plain parameters (D44). |
| stdio only, then stdio as the Docker default | Each stdio session is a fresh process, so the cache, request spacing and block pause reset every session. HTTP is the default everywhere; stdio is kept for debugging. |
| A bearer token for local use (first required, then optional) | Localhost plus the Host and Origin checks is enough; only an instance other machines can reach needs one (D43). |
| OAuth, and TLS inside crickey | A static token is enough for one person's hosted instance, and the hosting platform terminates TLS (D43). |
| Starting automatically at login | Manual start is enough. |
| Starting the pause after a block at 15 minutes | Shortened to start at 5 minutes. |
| A daily request budget | Unnecessary with on-demand fetching and the per-call page cap. |
| An on-disk cache, a config file, a Docker volume, CSV files, and `unblock` and `catalog` commands | Dropped to keep the server stateless. |
| A fixed 24-hour cache expiry | Replaced by the freshness rules above. |
| A stored filter catalogue | Replaced by built-in ID tables plus lookups when needed. |
| Fetching player profile pages from www.cricinfo.com | Blocked to scripts (R1), so getting through would mean working around bot protection. Their statistics match Statsguru's, so answers link to them instead. |
| Linking or fetching the new Statsguru on www.cricinfo.com/statsguru | crickey can't fetch or confirm it (D7, R1), the classic engine serves the same data, and repeated keys cover multi-value queries (R2). |
| A separate access-check step before building | Done during research (R1, R9); the live test re-checks it. |
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
| Golden questions in crickey's package, so tool descriptions could be built from them | The owner keeps them with the evals (D41). |
| Quoting every golden question in the description of the tool that answers it | It copied the golden questions into the code and a test (D41). |
| pytest tests of the evals: a live test of the cases' known answers, and offline tests of the checks, scorers, bridge and cases | Not needed, the owner said (18 Oct 2026, PR #67): the evals run on their own, and a changed case's known answer is checked live once, by hand (plan › Evals). |
| A `period` object, kept as it was or with each kind's fields spelled out in its description | Models still had to build a nested object, which some got wrong even when they saw its schema, and some renderers in Ollama hid its kinds (D42, R17). |
| Accepting all internationals combined as a second known answer for golden question 9 | It needs a second known answer checked live, and cases with alternative answers; the question names Tests and ODIs instead (#82). |
| Asking golden question 8 "in how many countries" | It tells the model what to count; the answer check reads the count in more forms instead (#82). |
| Running the evals in CI on GitHub's free runners | Only a model of about 4B fits: it takes hours per run, too long for 3 epochs in the 6-hour job limit, and its scores mostly measure the model (#65, R17). |
