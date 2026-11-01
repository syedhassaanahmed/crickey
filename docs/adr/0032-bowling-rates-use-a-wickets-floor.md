---
status: accepted
date: 2026-10-06
---

# Bowling rates use a wickets floor when needed

## Context and Problem Statement

Ranking or comparing bowlers on bowling average, economy rate or bowling strike rate needs a minimum: without one, Statsguru's table includes every bowler who bowled at all, which exceeds the limit of 4 result pages (D10). A floor that suits an all-time question is too high once a filter or a shorter period narrows the table. Which minimum do `leaderboard` and `better_than_player` use when the user gives none?

## Decision Drivers

* The table fits within 4 pages of 200 rows (D10).
* The floor is on the rate's natural denominator, the wickets.
* In a comparison, the bowler being compared always qualifies.

## Considered Options

* A wickets minimum, lower under a narrowing filter
* The same wickets minimum for every question
* No default minimum

## Decision Outcome

Chosen option: "A wickets minimum, lower under a narrowing filter", because it keeps rate tables within D10 for all-time and filtered questions alike, as [ADR 0029](0029-batting-rate-comparisons-use-a-runs-floor.md)'s runs floor does for batting.

* All-time questions with no filter use Tests 100, ODIs 100, T20Is 50, all T20 100 and combined internationals 200 wickets.
* A question with any narrowing filter (continent, host, opposition, ground, trophy, team, home or away, match result, or a period other than all time) uses Tests 30, ODIs 30, T20Is 20, all T20 50 and combined internationals 50.
* In `better_than_player`, the floor never exceeds the compared bowler's own wickets under the same filters, so they always qualify, except possibly a bowler with more than one team under a team filter ([ADR 0029](0029-batting-rate-comparisons-use-a-runs-floor.md)).

### Consequences

* Good, because rate tables stay within D10, measured for each format (R10).
* Bad, because a bowler with more than one team can need an explicit minimum under a team filter.

## Pros and Cons of the Options

### The same wickets minimum for every question

* Bad, because a filtered or shorter question leaves few bowlers above the all-time floor.

### No default minimum

* Bad, because the table includes everyone who bowled, far more than D10's 800 rows.

## More Information

* R10 has the measured row counts for the unfiltered floors, and for the filtered floors over a 10-year span. All T20's filtered floor is 50 because 30 gives too many rows.
* [ADR 0033](0033-count-leaderboards-have-no-default-floor.md): counts such as wickets themselves have no default floor in a leaderboard.
