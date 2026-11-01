---
status: accepted
date: 2026-10-28
---

# Periods are plain text

## Context and Problem Statement

The answer tools take a `period`: a player's career span, their first or last N years, a range of years or dates, or a season. Models have to pass it in a form every client can show them and they can write correctly. What type is `period`?

## Decision Drivers

* Every model sees the parameter's forms, whatever renders the tool for it.
* Plain parameters, so weaker models cope (D13).
* A wrong period is caught before any request.

## Considered Options

* A string in one of a few forms
* An object with a `kind` and that kind's fields
* An object with each kind's fields spelled out in its description

## Decision Outcome

Chosen option: "A string in one of a few forms", because every renderer shows a string, models often write periods as text anyway, and a string is the plain parameter D13 asks for.

* `period` takes forms such as "last 3 years", "2000 to 2009", "2000-01-01 to 2009-12-31" or "season 2011/12"; [plan.md › Calculations](../plan.md#calculations-inside-the-answer-tools) lists them all.
* crickey reads only those forms, and rejects anything else before any request, with a message that lists them.
* A bare year is the calendar year; a season needs the word "season" or the 2011/12 form.

### Consequences

* Good, because every model sees the same forms, in the parameter's description.
* Bad, because a client can't pass a structured period; the owner accepts this while crickey has few users.

## Pros and Cons of the Options

### An object with a `kind` and that kind's fields

* Bad, because some of Ollama's tool renderers drop an object's kinds: gpt-oss's shows it as a bare `object`, and gemma4's drops its variants (R17).
* Bad, because models still have to build a nested object, which some get wrong even when they see its schema (R17).

### An object with each kind's fields spelled out in its description

* Bad, because models still have to build the nested object.
