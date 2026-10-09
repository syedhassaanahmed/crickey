"""Inspect task: language models answer the golden questions through crickey (#18, D37).

    uv run --env-file evals/inspect.env inspect eval evals/golden_questions.py \
        --model ollama/qwen3:4b-instruct

evals/README.md says how to start crickey and Ollama first, and how to read the results.
"""

# ruff: noqa: E402

from __future__ import annotations

import sys
import time
from pathlib import Path

# Inspect imports this file by path, so the repo root makes the `evals` package importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inspect_ai import Epochs, Task, task
from inspect_ai.agent import AgentPrompt, as_solver, react
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import GenerateConfig
from inspect_ai.solver import Generate, Solver, TaskState, solver

from evals.bridge import CrickeyTools
from evals.cases import Case, load_cases
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

CRICKEY_URL = "http://127.0.0.1:8765/mcp"
EPOCHS = 3
# Tool definitions, server instructions and the question come to about 8,000 tokens (R17), and
# a CPU reads them slowly: minutes on the owner's laptop, about 15 on a GitHub runner. This bounds
# a model call with its retries; evals/inspect.env lets each HTTP request take as long (R17).
MODEL_TIMEOUT_SECONDS = 3600
# Room for several tool rounds, including a clarification and a second call (Imran Khan, R7).
MESSAGE_LIMIT = 40
# Models see crickey's whole answer, as an MCP client would show it; Inspect cuts at 16 KiB.
MAX_TOOL_OUTPUT_BYTES = 1024 * 1024


def system_prompt(instructions: str | None) -> str:
    """The prompt an MCP client would build: its own line, then the server's instructions."""
    lines = ["Answer the user's cricket statistics questions with the crickey tools."]
    if instructions:
        lines += ["", "Instructions from the crickey MCP server:", instructions]
    return "\n".join(lines)


@solver
def crickey_agent(crickey_url: str = CRICKEY_URL) -> Solver:
    """Inspect's ReAct agent with crickey's tools; it stops when the model stops calling tools."""
    tools = CrickeyTools(crickey_url)

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        await tools.connect()
        state.store.set(STARTED_KEY, time.monotonic())
        prompt = AgentPrompt(
            instructions=system_prompt(tools.instructions),
            handoff_prompt=None,
            assistant_prompt=None,
            submit_prompt=None,
        )
        agent = react(prompt=prompt, tools=[tools], submit=False)
        return await as_solver(agent)(state, generate)

    return solve


def sample(case: Case) -> Sample:
    return Sample(
        id=case.id,
        input=case.question,
        target="; ".join(" or ".join(facts) for facts in case.facts),
        metadata={"case": case.model_dump(mode="json")},
    )


@task
def golden_questions(crickey_url: str = CRICKEY_URL) -> Task:
    return Task(
        dataset=MemoryDataset([sample(case) for case in load_cases()], name="golden_questions"),
        solver=crickey_agent(crickey_url),
        scorer=[
            all_checks(),
            known_answer(),
            answer_tool(),
            arguments(),
            proof(),
            grounding(),
            cost(),
        ],
        epochs=Epochs(EPOCHS, "mean"),
        # One question at a time: a local model keeps the shared prompt between questions
        # (R17), and crickey sends its Statsguru requests one at a time anyway (D9).
        config=GenerateConfig(
            max_connections=1,
            timeout=MODEL_TIMEOUT_SECONDS,
            max_tool_output=MAX_TOOL_OUTPUT_BYTES,
        ),
        message_limit=MESSAGE_LIMIT,
        headline_metric="all_checks",
    )
