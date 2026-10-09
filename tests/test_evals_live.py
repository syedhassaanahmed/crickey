"""Live check of the eval cases' known answers (#18): each case's calls, asked of Statsguru."""

from __future__ import annotations

import pytest
from mcp import Client
from mcp.types import TextContent

from crickey.server import create_server
from crickey.settings import Settings
from evals import checks
from evals.cases import load_cases

pytestmark = [pytest.mark.anyio, pytest.mark.live]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_each_case_known_answer_holds_live() -> None:
    # One crickey for every case, so pages the cases share are fetched once (D9, D17).
    failures: dict[str, dict[str, str]] = {}
    async with Client(create_server(Settings()), read_timeout_seconds=600) as client:
        for case in load_cases():
            calls = []
            for call in case.calls:
                arguments = call.reference_arguments()
                result = await client.call_tool(str(call.tool), arguments)
                text = "\n\n".join(
                    item.text for item in result.content if isinstance(item, TextContent)
                )
                error = text if result.is_error else None
                calls.append(checks.ToolCall(str(call.tool), arguments, text, error))
            # The answer a model gives when it shows each answer_markdown as-is.
            answer = "\n\n".join(call.result for call in calls)
            transcript = checks.Transcript(case.question, tuple(calls), answer)
            failed = {
                name: result.explanation
                for name, check in checks.CHECKS.items()
                if not (result := check(case, transcript)).passed
            }
            if failed:
                failures[case.id] = failed

    assert failures == {}
