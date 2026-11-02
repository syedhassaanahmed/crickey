---
status: accepted
date: 2026-11-02
---

# CI asks live Statsguru daily

## Context and Problem Statement

crickey's tests use synthetic pages (D25). They check crickey's own code, but they keep passing when Cricinfo changes its HTML, renames a column or stops accepting a link, and crickey's answers then break until someone notices. The one live test parsed a results page of each stat type, ran only by hand and covered neither player pages, the player search nor the filter forms. How does the owner learn soon that Cricinfo changed something crickey relies on?

## Decision Drivers

* A change to Cricinfo's pages or links shows up within a day, before users find it.
* Live requests stay within D7–D12, and a block is never retried.
* A flaky or blocked Statsguru never stops a pull request from merging.

## Considered Options

* A daily live workflow that also runs on pull requests changing fetching or parsing
* Live tests by hand only
* Live tests on every pull request
* Live tests as a required check in `ci.yml`
* Committed snapshots of real pages

## Decision Outcome

Chosen option: "A daily live workflow that also runs on pull requests changing fetching or parsing", because it notices a change within a day for about 15 requests, and checks the changes most likely to break on real pages before they merge.

* `live.yml` runs `pytest -m live` every day, on demand, and on pull requests that change the fetcher, the parsers, URL building, name resolution, the ID tables, the live tests or `pyproject.toml`. With no lockfile (D23), the daily run also meets each new release of a dependency, such as the HTML parser. One run at a time, and it isn't a required check.
* The live tests share one `Fetcher` with default settings, so D9's spacing and D11's block pause cover them all. The log lists each request with its time and status.
* Besides a results page of each stat type, they ask `find_player` and each answer tool a question whose answer can't change (D37), which reads the player search, player pages with a split, a filter form and results pages through the tools that use them. A known answer also notices a column that keeps its name but changes its meaning.

### Consequences

* Good, because a change to Cricinfo's pages or links fails a run within a day, naming the tool and the missing fact or the parse error.
* Good, because pull requests that change fetching or parsing are checked against real pages before they merge.
* Bad, because about 15 requests a day reach results pages that robots.txt disallows, with no user's question behind them. They are the owner's own, at the owner's risk (D6).
* Bad, because Cricinfo may challenge GitHub's datacenter IPs more often than a home one (ADR 0043). A challenged run fails without saying anything about the pages, and isn't retried (D11).
* Bad, because Statsguru could correct an old figure, and the known answer then needs updating.
* Neutral, because GitHub turns off scheduled workflows in a repository with no activity for 60 days.

## Pros and Cons of the Options

### Live tests by hand only

* Good, because it sends no requests unless someone asks.
* Bad, because a change shows up only when someone thinks to run them, or a user hits it.

### Live tests on every pull request

* Bad, because most pull requests change only crickey's logic, which tests on synthetic pages cover, so their requests add little.

### Live tests as a required check in `ci.yml`

* Bad, because a block, a 429 or Cricinfo being down would stop every merge.

### Committed snapshots of real pages

* Bad, because no Cricinfo content is committed (D25), and snapshots can't show a change made after they were taken.

## More Information

* R1 has what GitHub's runner got from Statsguru on the first run.
* research.md's example data has the known answers the live tests check.
