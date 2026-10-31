---
status: accepted
date: 2026-10-06
---

# Count leaderboards have no default floor

## Context and Problem Statement

`leaderboard` ranks players on a count, such as runs, hundreds, wickets or catches, or on a rate. Statsguru sorts the whole table, and the leaderboard reads only its first page. What minimum does a count leaderboard use when the user gives none?

## Considered Options

* A minimum of 1 on the ranked field
* The metric registry's default floor, such as 100 Test wickets

## Decision Outcome

Chosen option: "A minimum of 1 on the ranked field", because for a count, a higher floor can only hide results: the table is already sorted by the count, and the leaderboard reads only the top of it.

* A count metric is one whose default minimum is on the ranked field itself: runs, hundreds, fifties, wickets, five-wicket hauls, ten-wicket matches, catches, stumpings and dismissals.
* With no minimum from the user, the count leaderboard uses a minimum of 1 on that field and says so in the answer.
* Rate and derived metrics keep their floors from the metric registry, [ADR 0032](0032-bowling-rates-use-a-wickets-floor.md) and [ADR 0035](0035-fielding-answers-use-one-set-of-floors.md).

### Consequences

* Good, because a narrow question still finds its leaders.
* Neutral, because the leaderboard still makes one request.

## Pros and Cons of the Options

### The metric registry's default floor

* Bad, because it can hide every result: in Test cricket in Asia for England or South Africa, the leaders have 92 wickets, so a floor of 100 returns nothing (R10).

## More Information

* R10 has the Asia table with a minimum of 1.
