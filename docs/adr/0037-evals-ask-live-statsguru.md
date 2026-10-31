---
status: accepted
date: 2026-10-29
---

# Evals ask live Statsguru, about answers that can't change

## Context and Problem Statement

crickey is built so that weaker models can answer cricket questions through its tools (D13). Whether they do depends on the models and on the tools' descriptions, which only a run through real models can show. How are crickey's answers evaluated?

## Decision Drivers

* The evals measure crickey's tools as models really use them, against the data users get.
* Statsguru's limits still hold (D7–D12).
* Scores are repeatable and need no other model to judge them.
* Statsguru's figures are never committed (D25).

## Considered Options

* Inspect AI, against live Statsguru, on demand with local models, scored by deterministic checks, with cases whose answers can't change
* Synthetic pages instead of live Statsguru
* Running the evals in CI on GitHub's free runners
* GitHub Models or Copilot for evals in CI
* pydantic-evals, DeepEval or promptfoo instead of Inspect AI
* An LLM judge instead of deterministic checks
* Cases with more than one known answer
* pytest tests of the evals

## Decision Outcome

Chosen option: "Inspect AI, against live Statsguru, on demand with local models, scored by deterministic checks, with cases whose answers can't change", because it measures the tools against the data users get, at no cost, with repeatable scores.

* The eval suite (`evals/`, Inspect AI, R17) asks the golden questions through language models against live Statsguru, within D7–D12's limits. A block stops the run.
* It runs on demand with local models, not in CI.
* Every case's answer can't change: the players have retired, and comparisons with other players use a period that has ended, such as the player's career span. Each case has one known answer, checked live and recorded in R10.
* Scores come from deterministic checks, with no LLM judge.
* Logs hold Statsguru's figures, so they're never committed.

### Consequences

* Good, because scores reflect live Statsguru and real models, and repeat from run to run within a model's own variation.
* Bad, because a run sends real requests to Statsguru, so it's slow and can be blocked.
* Bad, because nothing runs the evals automatically.

## Pros and Cons of the Options

### Synthetic pages instead of live Statsguru

* Bad, because the evals then measure crickey's test pages, not the data users get; the owner wants evals to ask live Statsguru. pytest keeps synthetic pages (D25).

### Running the evals in CI on GitHub's free runners

* Bad, because only a model of about 4B parameters fits: it takes hours per run, too long for 3 epochs in the 6-hour job limit, and its scores mostly measure the model (R17).

### GitHub Models or Copilot for evals in CI

* Bad, because GitHub Models was retired on 30 Jul 2026, and Copilot spends AI credits (R17).

### pydantic-evals, DeepEval or promptfoo instead of Inspect AI

* Bad, because Inspect AI has the agent, MCP tools, model providers and log viewer: pydantic-evals would need those built, DeepEval's MCP metrics need an LLM judge and its dashboards are hosted, and promptfoo is Node.js (R17).

### An LLM judge instead of deterministic checks

* Bad, because a judge would be weak with a local model.

### Cases with more than one known answer

* Bad, because each extra answer must be checked live too, and the cases would need alternative answers. Golden question 9 names Tests and ODIs instead of accepting all internationals combined as a second answer.

### pytest tests of the evals

* Neutral, because the owner judged them not needed: the evals run on their own, and a changed case's known answer is checked live once, by hand ([plan › Evals](../plan.md#evals)).

## More Information

* [evals/README.md](../../evals/README.md) has the command that runs the evals; plan.md › Evals has the design, and R17 the facts behind it.
* D38–D40 describe how the evals reach crickey, and [ADR 0041](0041-golden-questions-listed-once-with-the-evals.md) and [ADR 0045](0045-tool-descriptions-never-quote-an-eval-case.md) where the cases live and how they're kept apart from the tools.
