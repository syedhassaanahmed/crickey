---
status: accepted
date: 2026-10-29
---

# Series questions have their own answer tool

## Context and Problem Statement

Golden question 10 counts series: how many series of a kind were played in a country over a period. Statsguru's series view answers it in one request (R4), but only `query_stats` could reach that view, and models struggle to write its queries. How does crickey answer series questions?

## Decision Drivers

* Small models can answer it, with plain parameters (D13).
* The answer has a proof link (D16).

## Considered Options

* A `series_count` answer tool
* Answering through `query_stats`

## Decision Outcome

Chosen option: "A `series_count` answer tool", because it takes plain parameters and makes one request to Statsguru's series view, whose total is the count and whose link is the proof (R4, D16).

* `number_of_teams` takes "2", "3+", "3-4" or "5+", or the same in words, such as "three or more". It's text rather than an enum, so a model can write it its own way, and the description lists the values, since descriptions reach every model (R17). Statsguru can't tell three teams from four (R4), so "3" alone is an error that offers "3+" and "3-4".
* Series count as Statsguru counts them: a series counts when any of its matches meets the filters, so a co-hosted tournament counts for each host country, and `team` counts the series a team played in.
* The series view shows no names, so the tool reads them from the advanced form's series list, as lookups do (D18): one more request, the first time.
* The answer links only its proof, because models given each series' page cite those links instead (R17).

### Consequences

* Good, because small models answer series questions with plain parameters.
* Bad, because it's one more tool for models to choose from (D14).

## Pros and Cons of the Options

### Answering through `query_stats`

* Bad, because models struggle to write its queries, even with its fields named in the description (R17).
