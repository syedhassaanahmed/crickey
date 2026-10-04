# Working on crickey

crickey is an MCP server that answers cricket statistics questions from Cricinfo's Statsguru, with links that prove each answer. It's built one GitHub issue at a time, each in a fresh session.

## Read first
- [docs/plan.md](docs/plan.md): the goal, golden questions and design.
- [docs/decisions.md](docs/decisions.md): the numbered decisions (D1, D2, …) and the options that were dropped.
- [docs/research.md](docs/research.md): facts about Statsguru and the tools, in sections R1–R16.

Each fact lives in one of these files, and the others refer to it as D# or R#. Keep it that way: new facts go in research.md, new choices (with the reason) in decisions.md, and design changes in plan.md. If a decision gets in the way, raise it with the owner instead of working around it.

## Pick an issue
- Work on the issue the owner names. Otherwise, take the lowest-numbered open issue that isn't labelled `needs-owner` and whose "Blocked by" issues are all closed. `gh issue list --search "is:open -is:blocked -label:needs-owner"` lists those issues.
- Read it with `gh issue view <number>`. Do only that issue, and meet every acceptance criterion.
- If the issue is unclear or too big for one session, stop and say so.

## Build and test
- Use the versions in R15, through uv: `uv sync`, `uv run ruff format .`, `uv run ruff check .` and `uv run pytest`.
- Packages come from public PyPI (D23). If it can't be reached, ask the owner which index to use. Never commit an index URL or `uv.lock`.
- Tests use synthetic pages only (D25). Tests that reach Cricinfo are marked `live` and run only with `pytest -m live`.

## Fetching Cricinfo while developing
- Fetch only from stats.cricinfo.com (D7), at least 15 seconds apart (D9), with curl's User-Agent (D12), and only what the issue needs.
- On a 403 or a challenge page, stop and tell the owner. Never retry it or try to get around it (D7, D11).
- Keep fetched pages in `.local/`, which git ignores. Never commit them.

## Finish
- Work on a branch named `<issue number>-<short-name>`.
- Update the docs if the work changed a fact, a decision or the design.
- Open a pull request that says `Closes #<issue number>`. The owner reviews and merges it.
