---
status: accepted
date: 2026-10-08
---

# Fielding answers use one set of floors

## Context and Problem Statement

`leaderboard` and `better_than_player` take `discipline=fielding`, with six of Statsguru's columns as metrics: catches, catches as a fielder, catches as a wicketkeeper, stumpings, dismissals and dismissals per innings (R5, R8). Like batting and bowling rates, they need a default minimum that keeps tables within the limit of 4 result pages (D10). Which floors do fielding answers use, and does `player_record` take fielding too?

## Decision Drivers

* Tables fit within 4 pages of 200 rows (D10).
* One set of numbers is easier to keep right than one per metric.
* In a comparison, the player being compared always qualifies.

## Considered Options

* One set of floors for every fielding metric
* A floor for each fielding metric
* Fielding in `player_record` too

## Decision Outcome

Chosen option: "One set of floors for every fielding metric", because no fielding count exceeds dismissals, so a floor that keeps the dismissals table within D10 keeps every fielding table within it.

* Each count's floor is on its own field, and dismissals per innings uses a dismissals floor, the count of successes, as [ADR 0029](0029-batting-rate-comparisons-use-a-runs-floor.md) uses runs and [ADR 0032](0032-bowling-rates-use-a-wickets-floor.md) wickets.
* All-time questions with no filter use Tests 50, ODIs 50, T20Is 25, all T20 100 and combined internationals 100.
* A question with any narrowing filter ([ADR 0032](0032-bowling-rates-use-a-wickets-floor.md)'s list, including a period other than all time) uses Tests 20, ODIs 20, T20Is 20, all T20 50 and combined internationals 30.
* Count leaderboards keep a minimum of 1 ([ADR 0033](0033-count-leaderboards-have-no-default-floor.md)).
* In `better_than_player`, the floor never exceeds the compared player's own figure. As for batting, the tool reads that figure only when the player is missing, so comparisons where the player qualifies cost no extra request.
* Answers say that Statsguru's fielding figures count catches and stumpings but not run-outs, and say when catches or dismissals include those taken as a wicketkeeper.
* `player_record` stays batting and bowling.

### Consequences

* Good, because every fielding table stays within D10, measured for each format over a 10-year span (R10).
* Bad, because a player's own fielding record needs `query_stats`.

## Pros and Cons of the Options

### A floor for each fielding metric

* Bad, because it adds numbers to keep right without changing any answer: dismissals' floor already bounds every fielding count.

### Fielding in `player_record` too

* Bad, because `player_record`'s splits count hundreds or five-wicket hauls (D34), and fielding has no equivalent.

## More Information

* R10 has the measured row counts; T20Is' filtered floor is 20 because 10 gives too many rows.
