from __future__ import annotations

# ruff: noqa: E501
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from helpers import FakeClock, make_settings
from inspect_ai.model import (
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    ModelName,
    ModelOutput,
    execute_tools,
)
from inspect_ai.scorer import Target
from inspect_ai.solver import TaskState
from inspect_ai.tool import Tool, ToolCall, ToolCallError, ToolDef, ToolError, ToolParams
from inspect_ai.util import store
from mcp import Client

from crickey.fetcher import Fetcher, FetchResponse, MemoryPageSource
from crickey.query import StatsguruQuery
from crickey.server import SERVER_INSTRUCTIONS, create_server
from crickey.settings import Settings
from evals import checks
from evals.bridge import CALLS_KEY, CrickeyTools, StatsguruBlocked, inline_schema
from evals.cases import Case, load_cases
from evals.golden_questions import EPOCHS, golden_questions, system_prompt
from evals.scorers import (
    STARTED_KEY,
    all_checks,
    answer_tool,
    arguments,
    cost,
    grounding,
    known_answer,
    proof,
)

pytestmark = pytest.mark.anyio

FIXTURE = Path(__file__).parent / "fixtures" / "evals" / "two_bowlers.json"
SCENARIOS = list(json.loads(FIXTURE.read_text("utf-8"))["transcripts"])


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def synthetic() -> tuple[Case, dict[str, Any]]:
    data = json.loads(FIXTURE.read_text("utf-8"))
    return Case.model_validate(data["case"]), data


def synthetic_transcript(data: dict[str, Any], name: str) -> checks.Transcript:
    scenario = data["transcripts"][name]
    calls = tuple(
        checks.ToolCall(
            name=call["name"],
            arguments=call["arguments"],
            result=data["results"][call["result"]] if "result" in call else call["error"],
            error=call.get("error"),
        )
        for call in scenario["calls"]
    )
    return checks.Transcript(
        question=data["case"]["question"], calls=calls, answer=scenario["answer"]
    )


def results_page() -> str:
    return """
    <html><body>
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Player</th><th>Span</th><th>Mat</th><th>Runs</th><th>HS</th><th>Ave</th><th>100</th></tr>
    <tr class="data1"><td><a href="/ci/content/player/900001.html">AN Example</a> (XYZ)</td><td>2001-2010</td><td>10</td><td>900</td><td>101*</td><td>50.00</td><td>1</td></tr>
    </table>
    <table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>1</b> of <b>1</b></td></tr></table>
    </body></html>
    """


CLOCK_DATE = FakeClock().now().date()
BATTING_QUERY = {"class": 2, "type": "batting"}
BATTING_URL = StatsguruQuery(**BATTING_QUERY).results_url(as_of=CLOCK_DATE)


def offline_crickey(pages: dict[str, str | FetchResponse]):
    settings = make_settings()
    fetcher = Fetcher(settings, clock=FakeClock(), page_source=MemoryPageSource(pages))
    return create_server(settings, fetcher=fetcher)


async def tool_named(tools: CrickeyTools, name: str) -> Tool:
    return next(tool for tool in await tools.tools() if ToolDef(tool).name == name)


async def crickey_tools() -> dict[str, Any]:
    async with Client(create_server(Settings())) as client:
        return {tool.name: tool for tool in (await client.list_tools()).tools}


def test_cases_cover_every_golden_question_once() -> None:
    cases = load_cases()

    assert sorted(case.golden_question for case in cases) == list(range(1, 10))
    assert len({case.id for case in cases}) == len(cases)
    assert all(call.tool for case in cases for call in case.calls)


async def test_case_calls_use_tools_and_arguments_crickey_has() -> None:
    tools = await crickey_tools()

    for case in load_cases():
        assert case.tool in tools, case.id
        for call in case.calls:
            properties = tools[call.tool].input_schema["properties"]
            assert set(call.reference_arguments()) <= set(properties), (case.id, call)


def test_case_calls_resolve_as_crickey_resolves_them() -> None:
    for case in load_cases():
        for call in case.calls:
            class_id = checks._class_id(call.format)
            assert class_id is not None, (case.id, call.format)
            discipline = call.discipline or "batting"
            for choices in call.metrics:
                assert all(checks._metric_key(discipline, name) for name in choices), case.id
            for name, value in call.filters.items():
                assert checks._filter_values(name, value, class_id), (case.id, name, value)
            assert call.split_by is None or checks._same_split(call.split_by, call.split_by)


def test_each_case_accepts_its_own_reference_calls() -> None:
    for case in load_cases():
        calls = tuple(
            checks.ToolCall(name=str(call.tool), arguments=call.reference_arguments())
            for call in case.calls
        )
        transcript = checks.Transcript(question=case.question, calls=calls, answer="")

        assert checks.check_arguments(case, transcript).passed, case.id


@pytest.mark.parametrize(
    ("arguments", "passed"),
    [
        (
            {
                "player_name": "Jonty Rhodes",
                "format": "all formats",
                "discipline": "fielding",
                "metrics": ["catches as a fielder"],
            },
            True,
        ),
        (
            {
                "player_id": 46973,
                "format": 11,
                "discipline": "Fielding",
                "metrics": ["caught"],
                "period": {"kind": "career"},
            },
            True,
        ),
        (
            {
                "player_name": "Jonty Rhodes",
                "format": "all formats",
                "discipline": "fielding",
                "metrics": ["catches"],
                "period": {"kind": "all_time"},
            },
            False,
        ),
        (
            {
                "player_name": "Jonty Rhodes",
                "format": "ODI",
                "discipline": "fielding",
                "metrics": ["catches"],
            },
            False,
        ),
        (
            {
                "player_id": 99999,
                "format": "all formats",
                "discipline": "fielding",
                "metrics": ["catches"],
            },
            False,
        ),
    ],
)
def test_arguments_compare_as_crickey_resolves_them(arguments: dict, passed: bool) -> None:
    case = next(case for case in load_cases() if case.golden_question == 7)
    call = checks.ToolCall(name="better_than_player", arguments=arguments)
    transcript = checks.Transcript(question=case.question, calls=(call,), answer="")

    assert checks.check_arguments(case, transcript).passed is passed


@pytest.mark.parametrize("name", SCENARIOS)
def test_checks_on_saved_synthetic_transcripts(name: str) -> None:
    case, data = synthetic()
    transcript = synthetic_transcript(data, name)

    results = {check: run(case, transcript).passed for check, run in checks.CHECKS.items()}

    assert results == data["transcripts"][name]["expected"]
    assert checks.check_all(case, transcript).passed is all(results.values())


def test_checks_explain_what_failed() -> None:
    case, data = synthetic()

    assert (
        "8.75"
        in checks.check_grounding(
            case, synthetic_transcript(data, "works_out_its_own_figure")
        ).explanation
    )
    unsourced = checks.check_proof(
        case, synthetic_transcript(data, "cites_a_link_no_tool_returned")
    )
    assert "index.html?class=1;continent=2;type=bowling" in unsourced.explanation
    missing = checks.check_answer(case, synthetic_transcript(data, "leaves_out_a_fact"))
    assert "'Sample'" in missing.explanation and "'30.25'" in missing.explanation
    wrong_tool = checks.check_tool(case, synthetic_transcript(data, "compares_with_the_wrong_tool"))
    assert "better_than_player" in wrong_tool.explanation


def test_facts_match_whole_numbers_and_words() -> None:
    case, data = synthetic()
    transcript = synthetic_transcript(data, "passes")

    for answer, passed in [
        ("Example 21.5, Sample 30.25", True),
        ("EXAMPLE: 21.50 and **Sample** 30.25", True),
        ("Example 121.5, Sample 30.25", False),
        ("Examples 21.5, Sample 30.25", False),
        ("Example 21.5. Sample: 30.25.", True),
    ]:
        assert checks.check_answer(case, replace(transcript, answer=answer)).passed is passed, (
            answer
        )


def test_cost_counts_calls_errors_requests_tokens_and_time() -> None:
    _, data = synthetic()
    transcript = replace(
        synthetic_transcript(data, "asia_as_the_opposition"),
        tokens=12000,
        seconds=61.26,
        statsguru_requests=1,
        cached_pages=2,
    )

    assert checks.cost(transcript) == {
        "tool_calls": 2,
        "tool_errors": 1,
        "statsguru_requests": 1,
        "cached_pages": 2,
        "tokens": 12000,
        "seconds": 61.3,
    }


async def test_bridge_keeps_crickeys_whole_tool_schemas() -> None:
    tools = await crickey_tools()

    for tool in tools.values():
        inlined = inline_schema(tool.input_schema)
        params = ToolParams.model_validate(inlined)
        assert set(params.properties) == set(tool.input_schema["properties"]), tool.name
        assert "$ref" not in json.dumps(inlined) and "$defs" not in json.dumps(inlined)

    period = inline_schema(tools["player_record"].input_schema)["properties"]["period"]
    variants = [option for choice in period["anyOf"] for option in choice.get("anyOf", [])]
    kinds = {variant["properties"]["kind"]["enum"][0] for variant in variants}
    assert kinds == {"all_time", "career", "first_years", "last_years", "dates", "season"}
    query = ToolParams.model_validate(inline_schema(tools["query_stats"].input_schema))
    assert {"class", "type"} <= set(query.properties["query"].properties or {})


async def test_bridge_calls_crickey_and_records_each_calls_statsguru_requests() -> None:
    tools = CrickeyTools(offline_crickey({BATTING_URL: results_page()}))
    store().set(CALLS_KEY, [])

    query_stats = await tool_named(tools, "query_stats")
    fetched = await query_stats(query=BATTING_QUERY)
    cached = await query_stats(query=BATTING_QUERY)
    with pytest.raises(ToolError, match="limit"):
        await query_stats(query=BATTING_QUERY, limit=0)

    assert tools.instructions == SERVER_INSTRUCTIONS
    assert "AN Example" in str(fetched) and str(cached) == str(fetched)
    assert store().get(CALLS_KEY) == [
        {"tool": "query_stats", "requests": 1, "cached_pages": 0, "error": False},
        {"tool": "query_stats", "requests": 0, "cached_pages": 1, "error": False},
        {"tool": "query_stats", "requests": 0, "cached_pages": 0, "error": True},
    ]


async def test_bridge_tools_take_arguments_as_inspect_passes_them() -> None:
    tools = CrickeyTools(offline_crickey({BATTING_URL: results_page()}))
    call = ToolCall(id="call-1", function="query_stats", arguments={"query": BATTING_QUERY})

    result = await execute_tools([ChatMessageAssistant(content="", tool_calls=[call])], tools)

    (reply,) = result.messages
    assert isinstance(reply, ChatMessageTool)
    assert reply.error is None, reply.error
    assert "AN Example" in reply.text


async def test_bridge_stops_the_eval_when_statsguru_blocks_crickey() -> None:
    blocked = FetchResponse(url=BATTING_URL, status_code=403, headers={}, text="Forbidden")
    tools = CrickeyTools(offline_crickey({BATTING_URL: blocked}))

    query_stats = await tool_named(tools, "query_stats")
    with pytest.raises(StatsguruBlocked, match="blocked"):
        await query_stats(query=BATTING_QUERY)


def sample_state(name: str) -> TaskState:
    case, data = synthetic()
    scenario = data["transcripts"][name]
    messages: list[Any] = [
        ChatMessageSystem(content=system_prompt(SERVER_INSTRUCTIONS)),
        ChatMessageUser(content=case.question),
    ]
    for index, call in enumerate(scenario["calls"]):
        call_id = f"call-{index}"
        messages.append(
            ChatMessageAssistant(
                content="",
                tool_calls=[
                    ToolCall(id=call_id, function=call["name"], arguments=call["arguments"])
                ],
            )
        )
        error = ToolCallError("unknown", call["error"]) if "error" in call else None
        content = data["results"][call["result"]] if "result" in call else call["error"]
        messages.append(
            ChatMessageTool(
                content=content, tool_call_id=call_id, function=call["name"], error=error
            )
        )
    messages.append(ChatMessageAssistant(content=scenario["answer"]))
    return TaskState(
        model=ModelName("ollama/synthetic"),
        sample_id=case.id,
        epoch=1,
        input=case.question,
        messages=messages,
        output=ModelOutput.from_content("ollama/synthetic", scenario["answer"]),
        metadata={"case": case.model_dump(mode="json")},
        store={
            CALLS_KEY: [
                {"tool": "player_record", "requests": 1, "cached_pages": 0, "error": False},
                {"tool": "player_record", "requests": 0, "cached_pages": 1, "error": False},
            ],
            STARTED_KEY: time.monotonic() - 30,
        },
    )


async def test_scorers_score_the_sample_transcript() -> None:
    passing = sample_state("passes")
    failing = sample_state("asia_as_the_opposition")

    for scorer in (all_checks, known_answer, answer_tool, arguments, proof, grounding):
        assert (await scorer()(passing, Target(""))).value == 1.0, scorer
    assert (await all_checks()(failing, Target(""))).value == 0.0
    assert (await arguments()(failing, Target(""))).value == 0.0
    spent = (await cost()(failing, Target(""))).value
    assert isinstance(spent, dict)
    assert spent["tool_calls"] == 2 and spent["tool_errors"] == 1
    assert spent["statsguru_requests"] == 1 and spent["cached_pages"] == 1
    assert spent["seconds"] >= 30


def test_task_asks_every_case_one_question_at_a_time() -> None:
    task = golden_questions()

    assert [sample.id for sample in task.dataset] == [case.id for case in load_cases()]
    assert task.epochs == EPOCHS
    assert task.config.max_connections == 1
    assert task.config.max_tool_output is not None and task.config.max_tool_output >= 1024 * 1024


def test_system_prompt_passes_on_crickeys_instructions() -> None:
    prompt = system_prompt(SERVER_INSTRUCTIONS)

    assert prompt.endswith(SERVER_INSTRUCTIONS)
    assert system_prompt(None).startswith("Answer the user's cricket statistics questions")
