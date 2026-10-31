---
status: accepted
date: 2026-10-06
---

# Batting rate comparisons use a runs floor when needed

## Context and Problem Statement

`better_than_player` compares one batter's average or strike rate with everyone else's. Without a minimum, Statsguru's table includes every batter who batted at all, which exceeds the limit of 4 result pages (D10) before the comparison can be made. Which minimum does the tool use when the user gives none?

## Decision Drivers

* The table fits within 4 pages of 200 rows (D10).
* The batter being compared always qualifies.
* No extra request when the batter already qualifies.

## Considered Options

* A runs minimum
* An innings or balls-faced minimum
* No default minimum

## Decision Outcome

Chosen option: "A runs minimum", because it keeps rate tables within D10 and matches the golden-question sample in R10.

* When a batting comparison uses average or strike rate and the user gives no minimum, the tool uses a minimum of 1,000 runs, or 1,500 for combined internationals.
* The floor never exceeds the compared batter's own runs under the same filters, so they always qualify. The tool reads that figure only when the batter is missing from the qualifying rows, so comparisons where the batter already qualifies, such as golden questions 2 and 3, make no extra request.
* The player page has no team filter (R6), so under a team filter it gives the batter's runs across all their teams: exact for a batter with one team, an upper bound for one with more. If such a batter is still missing, the tool asks for an explicit minimum.

### Consequences

* Good, because rate tables stay within D10.
* Good, because the batter being compared always qualifies, except possibly one with more than one team under a team filter.
* Bad, because a batter with more than one team can need an explicit minimum under a team filter.

## Pros and Cons of the Options

### An innings or balls-faced minimum

* Bad, because they make broad tables: an innings minimum of 20, or 30 for all T20 and combined internationals, gives over a thousand rows in every format (R10).

### No default minimum

* Bad, because the table includes everyone who batted, far more than D10's 800 rows.

## More Information

* R10 has the golden-question sample and the measured row counts.
* [ADR 0032](0032-bowling-rates-use-a-wickets-floor.md) and [ADR 0035](0035-fielding-answers-use-one-set-of-floors.md) apply the same reasoning to bowling and fielding.
