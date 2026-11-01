# Working on crickey

crickey is an MCP server that answers cricket statistics questions from Cricinfo's Statsguru, with links that prove each answer. It's built one GitHub issue at a time, each in a fresh session.

## Read first
- [docs/plan.md](docs/plan.md): the goal and design.
- [evals/golden_questions.toml](evals/golden_questions.toml): the golden questions, the one place they're listed (D41), and their held-out cases. Elsewhere, refer to one by its number. Never copy a case's wording, players or values into tool descriptions or the server instructions (D45).
- [docs/decisions.md](docs/decisions.md): the numbered decisions (D1, D2, …), with a record in [docs/adr/](docs/adr/) for each big one, and the options that were dropped.
- [docs/research.md](docs/research.md): facts about Statsguru and the tools, in sections R1–R17.

Each fact lives in one of these files, and the others refer to it as D# or R#. Keep it that way: new facts go in research.md, new choices (with the reason) in decisions.md, and design changes in plan.md. If a decision gets in the way, raise it with the owner instead of working around it.

The docs say only where crickey stands now. When something changes, rewrite the text in place, without old versions, before/after comparisons, dated notes or the story of how a result was found; where an old result is the reason for a current decision, state the reason once. Describe machines only as far as a measurement needs to be reproduced, such as "an Apple Silicon laptop with 36 GB of unified memory". A big or complex decision gets an ADR, as decisions.md's header explains. Code comments say the reason in words rather than citing a D# or R#.

## Pick an issue
- Work on the issue the owner names. Otherwise, take the lowest-numbered open issue that isn't labelled `needs-owner` and whose "Blocked by" issues are all closed. `gh issue list --search "is:open -is:blocked -label:needs-owner"` lists those issues.
- If the owner asks for a change that has no issue, open one first, with a user story, the context (facts and where they come from) and acceptance criteria as checkboxes. When the owner has a choice to make, the issue lists the options.
- Read it with `gh issue view <number>`. Do only that issue, and meet every acceptance criterion.
- If the issue is unclear or too big for one session, stop and say so.

## Keep crickey small
- Leave nothing stale. When a change leaves code, a setting, a test, a passage of the docs or a decision with no use, remove it in the same pull request.
- crickey has few users, so breaking changes are fine. Don't keep compatibility shims or deprecated aliases.
- Prefer the standard library, or a library crickey already uses (D21), to homegrown code.
- Keep settings, tools and parameters few. Before adding one, check whether an existing one can do its job.

## Build and test
- Use the versions in R15, through uv: `uv sync`, `uv run ruff format .`, `uv run ruff check .` and `uv run pytest`.
- Packages come from public PyPI (D23). If it can't be reached, ask the owner which index to use. Never commit an index URL or `uv.lock`.
- Tests use synthetic pages only (D25). Tests that reach Cricinfo are marked `live` and run only with `pytest -m live`.
- Each test checks crickey's own code in a way no other test does. Don't write tests whose dependencies are mostly mocked or that check what their own fake returned, tests that repeat another test's check (the end-to-end tests' included), or tests that only restate a constant or a text from the code, such as a description or the version. Fakes that only replace I/O, such as synthetic pages or a fake clock, are fine.
- Keep coverage of `src/crickey` at 95% or more. Measure it with `uv run --with coverage coverage run --source=src/crickey -m pytest`, then `uv run --with coverage coverage report`, without adding coverage.py to the project (D21).
- The evals ([evals/README.md](evals/README.md)) ask live Statsguru through language models, so run them only when an issue needs it, and never commit their logs (D37).

## Fetching Cricinfo while developing
- Fetch only from stats.cricinfo.com (D7), and only what the issue needs.
- Use curl, at least 15 seconds apart, or, in code, crickey's `Fetcher` with its default settings, as `scripts/statsguru_pages.py` does: it spaces requests as D9 says. Both send curl's User-Agent (D12). Never write your own fetch loop or shorten the spacing, even for a quick check.
- On a 403 or a challenge page, stop and tell the owner. Never retry it or try to get around it (D7, D11).
- Keep fetched pages in `.local/`, which git ignores. Never commit them.

## Finish
- Work on a branch from `main` named `<issue number>-<short-name>`.
- Update the docs if the work changed a fact, a decision or the design.
- Open a pull request that says `Closes #<issue number>`, lists what changed and why, and says whether models see anything different: a tool's name, description or schema, or the server instructions.
- The owner reviews it and merges it. If they say it's good to go instead, squash-merge the commit they reviewed (`gh pr merge --squash --delete-branch --match-head-commit <sha>`), with `(#<pull request>)` at the end of the subject and `Closes #<issue number>` in the body, and check that CI passes on `main`.
- Dependabot's pull requests have no issue. When the owner says one is good to go, merge it the same way, without the `Closes` line. If it moves past a version R15 names, update R15 in it first.

## Release
- When the owner asks for a release, open a pull request that bumps the version in `pyproject.toml` and, for a new minor version, the version and image tags in the README. It needs no issue, and it lists the changes since the last release.
- Once it's merged, tag `main` with an annotated tag `v<version>` and push it. `release.yml` publishes the image and the GitHub Release ([plan.md](docs/plan.md) › CI pipeline); check that it passes.

## Talking to the owner
- Keep answers, issues and pull requests short and plain. When the owner has a choice to make, give each option in a line that says what changes for them, and recommend one.
- Back each claim with the test, diff or measurement that shows it.
