"""The golden questions and their eval cases, from golden_questions.toml (D37, D41)."""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

GOLDEN_QUESTIONS_FILE = Path(__file__).with_name("golden_questions.toml")

# The answer tools' filter parameters (plan › MCP tools).
FILTERS = ("team", "opposition", "host_country", "continent", "ground", "trophy")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedPlayer(_Strict):
    id: int
    # Names that identify the player in `player_name`, matched as whole words.
    names: tuple[str, ...] = Field(min_length=1)


class ExpectedPeriod(_Strict):
    # "career" also accepts no period, which the answer tools read as the player's career, and
    # with start and end, the span crickey resolves for that career (R10).
    kind: Literal["career", "all_time", "dates", "first_years", "last_years"]
    start: date | None = None
    end: date | None = None
    years: int | None = None

    def text(self) -> str:
        """The period as the answer tools take it (D42), such as "last 3 years"."""
        if self.kind == "dates":
            return f"{self.start} to {self.end}"
        if self.years is not None:
            return f"{self.kind.removesuffix('_years')} {self.years} years"
        return self.kind.replace("_", " ")


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
    number_of_teams: str | None = None

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
            arguments["period"] = self.period.text()
        arguments.update(self.filters)
        if self.minimum is not None:
            arguments["minimum"] = (
                int(self.minimum) if self.minimum == int(self.minimum) else self.minimum
            )
        if self.split_by is not None:
            arguments["split_by"] = self.split_by
        if self.number_of_teams is not None:
            arguments["number_of_teams"] = self.number_of_teams
        return arguments


class Case(_Strict):
    # The golden question's number in the docs, and its general form. A held-out case takes the
    # general form from its golden case.
    number: int = Field(ge=1)
    golden_question: str | None = None
    id: str
    # A held-out case asks the same kind of question in other words, about other players,
    # teams, formats or periods. Nothing in crickey quotes it, so it shows how models handle a
    # question they haven't been shown (D45).
    held_out: bool = False
    # What the eval asks: the golden question with real players and an answer that can't change.
    question: str
    # The answer tool the question needs (plan › MCP tools).
    tool: str
    calls: tuple[ExpectedCall, ...] = Field(min_length=1)
    # The known answer (R10): each entry lists the texts that count, and the answer needs one
    # of each.
    facts: tuple[tuple[str, ...], ...] = Field(min_length=1)
    fixed_because: str

    @model_validator(mode="after")
    def _general_form_on_golden_cases(self) -> Case:
        if not self.held_out and self.golden_question is None:
            raise ValueError(f"{self.id}: a golden case needs golden_question")
        return self

    @model_validator(mode="after")
    def _calls_default_to_the_case_tool(self) -> Case:
        calls = tuple(
            call if call.tool is not None else call.model_copy(update={"tool": self.tool})
            for call in self.calls
        )
        object.__setattr__(self, "calls", calls)
        return self


def load_cases(path: Path = GOLDEN_QUESTIONS_FILE) -> list[Case]:
    data = tomllib.loads(path.read_text("utf-8"))
    repeated = [
        item["id"] for item in data["case"] if item.get("held_out") and "golden_question" in item
    ]
    if repeated:
        raise ValueError(f"held-out cases take golden_question from their golden case: {repeated}")
    cases = [Case.model_validate(item) for item in data["case"]]
    general_forms = {case.number: case.golden_question for case in cases if not case.held_out}
    if len(general_forms) != sum(not case.held_out for case in cases):
        raise ValueError("each golden question has one golden case")
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("case ids must be unique")
    missing = [case.id for case in cases if case.number not in general_forms]
    if missing:
        raise ValueError(f"held-out cases without a golden case: {missing}")
    return [
        case.model_copy(update={"golden_question": general_forms[case.number]})
        if case.held_out
        else case
        for case in cases
    ]
