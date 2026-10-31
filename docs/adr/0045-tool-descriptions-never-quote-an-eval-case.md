---
status: accepted
date: 2026-10-30
---

# Tool descriptions never quote an eval case, and held-out cases check each change

## Context and Problem Statement

The tools' descriptions show models example questions, and the evals ask the golden questions ([ADR 0037](0037-evals-ask-live-statsguru.md)). If a description quotes an eval case, a model can pass by matching the question to the example, and a fix made for one failing case can't be told apart from fitting that case. How are descriptions and eval cases kept apart?

## Decision Drivers

* Scores show how models handle questions they haven't been shown.
* A change made for a failing case helps other questions of its kind.

## Considered Options

* Descriptions that never quote an eval case, plus held-out cases
* Quoting each golden question in the description of the tool that answers it
* A test that fails when a description quotes an eval case

## Decision Outcome

Chosen option: "Descriptions that never quote an eval case, plus held-out cases", because it keeps the evals measuring questions models haven't seen, and leaving the quotes out costs little: removing them changed scores by less than a run's own variation (R17).

* Tool descriptions, parameter descriptions and the server instructions never quote a golden question or an eval case, held-out or not: no run of 5 or more words in common, and none of an eval case's players or values. Their examples show the same kinds of question about other players, formats, places and periods.
* Each golden question has at least one held-out case in [evals/golden_questions.toml](../../evals/golden_questions.toml): the same kind of question in other words, about other players, teams, formats or periods, with an answer that can't change. The eval reports held-out cases apart from the golden ones.
* A change made for a failing case is checked on the held-out cases too, and never copies a case's wording, players or values.

### Consequences

* Good, because held-out scores show whether a change generalizes.
* Bad, because every new example has to be checked against every eval case.

## Pros and Cons of the Options

### Quoting each golden question in the description of the tool that answers it

* Bad, because it copies the golden questions into the code, and a model can pass by matching the question to its example.

### A test that fails when a description quotes an eval case

* Neutral, because the owner judged it not needed.

## More Information

* R17 has how much the scores changed when the examples stopped quoting the golden questions, and what models copy from examples.
