---
status: accepted
date: 2026-11-01
---

# Short bursts, then 15 seconds between requests

## Context and Problem Statement

Statsguru's robots.txt asks for 15 seconds between requests, and Statsguru answers sustained faster requests with a 429 (R1). With every request 15 s apart, an answer that needs 4 uncached requests takes about a minute, and most answers that reach Statsguru need 1 to 4 (R17). How can crickey answer faster without asking more of Statsguru over time?

## Decision Drivers

* Over time, crickey sends no more requests than robots.txt's crawl delay allows.
* Answers that need a few requests come back in seconds.
* crickey never tests Statsguru's limits on purpose.

## Considered Options

* Short bursts at the crawl delay's average
* 15 seconds between every request
* A lower default spacing, such as 5 seconds
* Spacing that speeds up until Statsguru answers 429
* Fewer requests per answer

## Decision Outcome

Chosen option: "Short bursts at the crawl delay's average", because it makes most answers fast while the long-run rate stays one request per 15 s.

* After a quiet spell, up to 4 requests go 3 s apart. Each request uses one unit of an allowance that refills one unit per `CRICKEY_MIN_INTERVAL`, up to 4, so any stretch of time holds at most 3 requests more than strict spacing would. The burst stays far from the dozen fast requests that drew a 429, and it's fixed rather than a setting, because the spacing already is one.
* A 429, a server error or a timeout empties the allowance, so the requests after it go `CRICKEY_MIN_INTERVAL` apart until it refills.

### Consequences

* Good, because an answer that needs 4 requests waits 9 s for spacing instead of 45 s.
* Good, because the long-run rate doesn't change, so long runs such as the evals ask no more of Statsguru.
* Bad, because a burst goes faster than robots.txt's crawl delay for a few seconds.

## Pros and Cons of the Options

### 15 seconds between every request

* Good, because it follows robots.txt's crawl delay to the letter.
* Bad, because each extra request adds 15 s to an answer.

### A lower default spacing, such as 5 seconds

* Bad, because every user would ask more of Statsguru than the crawl delay allows, all the time, and sustained fast requests draw 429s.

### Spacing that speeds up until Statsguru answers 429

* Bad, because it tests Statsguru's limit on purpose, and a block pauses crickey for minutes to hours (D11).

### Fewer requests per answer

* Bad, because the answer tools already skip every request they can, so little is left to cut.
