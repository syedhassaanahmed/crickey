"""Inspect scorers for the eval's checks (#18): each turns a sample into a transcript first."""

from __future__ import annotations

import time
from collections.abc import Callable

from inspect_ai.model import ChatMessageAssistant, ChatMessageTool
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer, stderr
from inspect_ai.solver import TaskState

from evals import checks
from evals.bridge import CALLS_KEY
from evals.cases import Case

# The solver stores when the sample started, for the cost check's time.
STARTED_KEY = "crickey_started"


def transcript_from_state(state: TaskState) -> checks.Transcript:
    results = {
        message.tool_call_id: message
        for message in state.messages
        if isinstance(message, ChatMessageTool)
    }
    calls: list[checks.ToolCall] = []
    for message in state.messages:
        if not isinstance(message, ChatMessageAssistant):
            continue
        for call in message.tool_calls or []:
            result = results.get(call.id)
            if result is None:
                # A result goes missing when its round passes the message limit.
                error: str | None = "No result reached the model."
            else:
                error = result.error.message if result.error is not None else None
            calls.append(
                checks.ToolCall(
                    name=call.function,
                    arguments=call.arguments or {},
                    result=result.text if result is not None else "",
                    error=error,
                )
            )
    records = state.store.get(CALLS_KEY, [])
    started = state.store.get(STARTED_KEY)
    return checks.Transcript(
        question=state.input_text,
        calls=tuple(calls),
        answer=state.output.completion if state.output is not None else "",
        tokens=state.token_usage,
        seconds=time.monotonic() - started if started is not None else 0.0,
        statsguru_requests=sum(record["requests"] for record in records),
        cached_pages=sum(record["cached_pages"] for record in records),
    )


def _case(state: TaskState) -> Case:
    return Case.model_validate(state.metadata["case"])


def _check_scorer(check: Callable[[Case, checks.Transcript], checks.CheckResult]) -> Scorer:
    async def score(state: TaskState, target: Target) -> Score:
        result = check(_case(state), transcript_from_state(state))
        return Score(
            value=1.0 if result.passed else 0.0,
            answer=state.output.completion if state.output is not None else None,
            explanation=result.explanation,
        )

    return score


@scorer(metrics=[mean(), stderr()])
def known_answer() -> Scorer:
    """The final answer contains the known answer's facts."""
    return _check_scorer(checks.check_answer)


@scorer(metrics=[mean(), stderr()])
def answer_tool() -> Scorer:
    """The answer tool the question needs was called, rather than only query_stats."""
    return _check_scorer(checks.check_tool)


@scorer(metrics=[mean(), stderr()])
def arguments() -> Scorer:
    """Format, discipline, metrics, filters and period match the question."""
    return _check_scorer(checks.check_arguments)


@scorer(metrics=[mean(), stderr()])
def proof() -> Scorer:
    """The answer cites a Statsguru link, and only links that tools returned (D16)."""
    return _check_scorer(checks.check_proof)


@scorer(metrics=[mean(), stderr()])
def grounding() -> Scorer:
    """The answer's figures appear in tool results or the question (D13)."""
    return _check_scorer(checks.check_grounding)


@scorer(metrics=[mean(), stderr()])
def all_checks() -> Scorer:
    """The headline: the question passes every check above."""
    return _check_scorer(checks.check_all)


@scorer(metrics=[{key: [mean()] for key in checks.COST_KEYS}])
def cost() -> Scorer:
    """Tool calls, tool errors, Statsguru requests and cached pages, tokens and seconds."""

    async def score(state: TaskState, target: Target) -> Score:
        return Score(value=checks.cost(transcript_from_state(state)))

    return score
