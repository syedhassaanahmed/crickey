---
status: accepted
date: 2026-10-30
---

# The golden questions are listed once, with the evals

## Context and Problem Statement

The golden questions are the questions crickey must answer, and the evals ask them (D37). Plan, research, tests and tool descriptions all need to refer to them. Where are they listed, and how do other files refer to them?

## Considered Options

* Listed once, in evals/golden_questions.toml
* Golden questions in crickey's package, so tool descriptions could be built from them
* Written out wherever they're used

## Decision Outcome

Chosen option: "Listed once, in evals/golden_questions.toml", because changing a golden question then means editing one file, and the file belongs to the evals that ask them.

* [evals/golden_questions.toml](../../evals/golden_questions.toml) lists each golden question with its number, its general form and its eval case (D37), and its held-out cases ([ADR 0045](0045-tool-descriptions-never-quote-an-eval-case.md)).
* Other files refer to a golden question by its number instead of quoting it.
* crickey's code doesn't read the file, and the image doesn't ship it.
* The answer tools' descriptions keep example questions written for models, which never quote an eval case ([ADR 0045](0045-tool-descriptions-never-quote-an-eval-case.md)). The evals measure whether they're enough, and failures become issues (D14).

### Consequences

* Good, because each golden question has one source.
* Bad, because a reader has to open the file to see a question that another file cites by number.

## Pros and Cons of the Options

### Golden questions in crickey's package

* Bad, because the owner keeps them with the evals, and crickey would ship test data in its image.

### Written out wherever they're used

* Bad, because changing one means finding and editing every copy.

## More Information

* AGENTS.md tells contributors to refer to a golden question by its number.
