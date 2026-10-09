"""Eval cases: the golden questions with real players filled in and answers that can't change."""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CASES_FILE = Path(__file__).with_name("cases.toml")

# The answer tools' filter parameters (plan › MCP tools).
FILTERS = ("team", "opposition", "host_country", "continent", "ground", "trophy")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedPlayer(_Strict):
    id: int
    # Names that identify the player in `player_name`, matched as whole words.
    names: tuple[str, ...] = Field(min_length=1)


class ExpectedPeriod(_Strict):
    # "career" also accepts no period, which the answer tools read as the player's career.
    kind: Literal["career", "all_time", "dates", "first_years", "last_years"]
    start: date | None = None
    end: date | None = None
    years: int | None = None


class ExpectedCall(_Strict):
    """A call the question needs, with the arguments that decide its answer."""

    tool: str | None = None
    player: ExpectedPlayer | None = None
    format: str | None = None
    discipline: str | None = None
    # One entry per metric the call must ask for, each listing the metrics that count.
    metrics: tuple[tuple[str, ...], ...] = ()
    period: ExpectedPeriod | None = None
    filters: dict[str, str] = {}
    minimum: float | None = None
    split_by: str | None = None

    @model_validator(mode="after")
    def _known_filters(self) -> ExpectedCall:
        unknown = set(self.filters) - set(FILTERS)
        if unknown:
            raise ValueError(f"unknown filters: {sorted(unknown)}")
        return self

    def reference_arguments(self) -> dict[str, Any]:
        """The arguments of a correct call, for confirming the known answer live."""
        arguments: dict[str, Any] = {}
        if self.player is not None:
            arguments["player_id"] = self.player.id
            arguments["player_name"] = self.player.names[0]
        if self.format is not None:
            arguments["format"] = self.format
        if self.discipline is not None:
            arguments["discipline"] = self.discipline
        if self.metrics:
            metrics = [choices[0] for choices in self.metrics]
            if self.tool == "leaderboard":
                arguments["metric"] = metrics[0]
            else:
                arguments["metrics"] = metrics
        if self.period is not None and self.period.kind != "career":
            arguments["period"] = self.period.model_dump(mode="json", exclude_none=True)
        arguments.update(self.filters)
        if self.minimum is not None:
            arguments["minimum"] = (
                int(self.minimum) if self.minimum == int(self.minimum) else self.minimum
            )
        if self.split_by is not None:
            arguments["split_by"] = self.split_by
        return arguments


class Case(_Strict):
    id: str
    golden_question: int = Field(ge=1, le=9)
    question: str
    # The answer tool the question needs (plan › MCP tools).
    tool: str
    calls: tuple[ExpectedCall, ...] = Field(min_length=1)
    # The known answer (R10): each entry lists the texts that count, and the answer needs one
    # of each.
    facts: tuple[tuple[str, ...], ...] = Field(min_length=1)
    fixed_because: str

    @model_validator(mode="after")
    def _calls_default_to_the_case_tool(self) -> Case:
        calls = tuple(
            call if call.tool is not None else call.model_copy(update={"tool": self.tool})
            for call in self.calls
        )
        object.__setattr__(self, "calls", calls)
        return self


def load_cases(path: Path = CASES_FILE) -> list[Case]:
    data = tomllib.loads(path.read_text("utf-8"))
    return [Case.model_validate(item) for item in data["case"]]
