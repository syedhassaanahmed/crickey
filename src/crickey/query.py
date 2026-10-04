from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self
from urllib.parse import quote_plus

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from crickey import id_tables

STATS_BASE = "https://stats.cricinfo.com/ci/engine"

StatType = Literal["batting", "bowling", "fielding", "allround", "fow", "team", "aggregate"]
View = Literal[
    "",
    "innings",
    "match",
    "series",
    "ground",
    "host",
    "opposition",
    "year",
    "season",
    "results",
    "extras",
    "extras_innings",
    "awards",
]

CLASS_LABELS = {
    1: "Test",
    2: "ODI",
    3: "T20I",
    6: "T20",
    11: "Test/ODI/T20I",
}

TYPE_LABELS = {
    "batting": "batting",
    "bowling": "bowling",
    "fielding": "fielding",
    "allround": "all-round",
    "fow": "partnerships",
    "team": "team",
    "aggregate": "aggregate",
}

FIELD_LABELS = {
    "runs": "runs",
    "batting_average": "average",
    "batting_strike_rate": "strike rate",
    "hundreds": "hundreds",
    "wickets": "wickets",
    "bowling_average": "bowling average",
    "economy_rate": "economy rate",
    "matches": "matches",
    "player": "player",
    "start": "start date",
}

COMMON_VIEWS = {"", "innings", "match", "series", "ground", "host", "opposition", "year", "season"}
TYPE_VIEWS: dict[str, set[str]] = {
    "batting": COMMON_VIEWS,
    "bowling": COMMON_VIEWS,
    "fielding": COMMON_VIEWS,
    "allround": COMMON_VIEWS | {"results", "awards"},
    "fow": COMMON_VIEWS,
    "team": COMMON_VIEWS | {"results", "extras", "extras_innings"},
    "aggregate": {"", "match", "results", "series", "ground", "host", "year", "season", "extras"},
}

SHARED_FILTER_FIELDS = {
    "team",
    "opposition",
    "home_or_away",
    "host",
    "ground",
    "spanquickpick",
    "season",
    "result",
    "continent",
    "series",
    "trophy",
    "tournament_type",
    "final_type",
    "floodlit",
    "toss",
    "debut_or_last",
    "agemin1",
    "agemax1",
    "agequickpick",
    "search_player",
    "player_involve",
    "player_involve_type",
    "search_captain",
    "captain_involve",
    "captain_involve_type",
    "qualquickpick",
}
TYPE_FILTER_FIELDS: dict[str, set[str]] = {
    "batting": SHARED_FILTER_FIELDS
    | {
        "batting_fielding_first",
        "captain",
        "keeper",
        "batting_hand",
        "innings_number",
        "runsmin1",
        "runsmax1",
        "runsquickpick",
        "batting_positionmin1",
        "batting_positionmax1",
        "batting_positionquickpick",
        "outs",
        "dismissal",
    },
    "bowling": SHARED_FILTER_FIELDS
    | {
        "batting_fielding_first",
        "captain",
        "keeper",
        "innings_number",
        "bowling_hand",
        "bowling_pacespin",
        "ballsmin1",
        "ballsmax1",
        "ballsquickpick",
        "concededmin1",
        "concededmax1",
        "concededquickpick",
        "wicketsmin1",
        "wicketsmax1",
        "wicketsquickpick",
        "bowling_positionmin1",
        "bowling_positionmax1",
        "bowling_positionquickpick",
    },
    "fielding": SHARED_FILTER_FIELDS
    | {
        "batting_fielding_first",
        "captain",
        "keeper",
        "innings_number",
        "caughtmin1",
        "caughtmax1",
        "caughtquickpick",
        "stumpedmin1",
        "stumpedmax1",
        "stumpedquickpick",
    },
    "allround": SHARED_FILTER_FIELDS
    | {
        "batting_fielding_first",
        "captain",
        "keeper",
        "batting_hand",
        "innings_number",
        "runsmin1",
        "runsmax1",
        "runsquickpick",
        "batting_positionmin1",
        "batting_positionmax1",
        "batting_positionquickpick",
        "outs",
        "dismissal",
        "bowling_hand",
        "bowling_pacespin",
        "ballsmin1",
        "ballsmax1",
        "ballsquickpick",
        "concededmin1",
        "concededmax1",
        "concededquickpick",
        "wicketsmin1",
        "wicketsmax1",
        "wicketsquickpick",
        "bowling_positionmin1",
        "bowling_positionmax1",
        "bowling_positionquickpick",
        "caughtmin1",
        "caughtmax1",
        "caughtquickpick",
        "stumpedmin1",
        "stumpedmax1",
        "stumpedquickpick",
    },
    "fow": SHARED_FILTER_FIELDS
    | {
        "batting_fielding_first",
        "innings_number",
        "partnership_runsmin1",
        "partnership_runsmax1",
        "partnership_runsquickpick",
        "partnership_wicketmin1",
        "partnership_wicketmax1",
        "partnership_wicketquickpick",
        "fow_type",
    },
    "team": SHARED_FILTER_FIELDS
    | {
        "batting_fielding_first",
        "innings_number",
        "runsmin1",
        "runsmax1",
        "runsquickpick",
        "wicketsmin1",
        "wicketsmax1",
        "wicketsquickpick",
        "ballsmin1",
        "ballsmax1",
        "ballsquickpick",
        "event",
        "team_view",
    },
    "aggregate": SHARED_FILTER_FIELDS
    - {"search_captain", "captain_involve", "captain_involve_type"},
}

BAT_OVERALL = {
    "matches",
    "innings",
    "notouts",
    "outs",
    "runs",
    "minutes",
    "balls_faced",
    "batting_average",
    "batting_strike_rate",
    "hundreds",
    "fifty_plus",
    "ducks",
    "fours",
    "sixes",
}
BAT_BY_VIEW = {
    "": BAT_OVERALL,
    "ground": BAT_OVERALL,
    "host": BAT_OVERALL,
    "opposition": BAT_OVERALL,
    "series": BAT_OVERALL,
    "year": BAT_OVERALL | {"year"},
    "season": BAT_OVERALL | {"season"},
    "innings": {"batted_score", "minutes", "balls_faced", "fours", "sixes", "batting_strike_rate"},
    "match": BAT_OVERALL | {"high_score"},
}
BOWL_OVERALL = {
    "matches",
    "innings_bowled",
    "balls",
    "overs",
    "maidens",
    "conceded",
    "wickets",
    "bowling_average",
    "economy_rate",
    "bowling_strike_rate",
    "four_plus_wickets",
    "five_wickets",
    "ten_wickets",
}
BOWL_BY_VIEW = {
    "": BOWL_OVERALL,
    "ground": BOWL_OVERALL,
    "host": BOWL_OVERALL,
    "opposition": BOWL_OVERALL,
    "series": BOWL_OVERALL,
    "year": BOWL_OVERALL | {"year"},
    "season": BOWL_OVERALL | {"season"},
    "innings": {
        "overs",
        "maidens",
        "conceded",
        "wickets",
        "bowling_average",
        "economy_rate",
        "bowling_strike_rate",
        "bowling_position",
    },
    "match": BOWL_OVERALL,
}
FIELD_OVERALL = {
    "matches",
    "matches_keeper",
    "matches_fielder",
    "innings_fielded",
    "dismissals",
    "caught",
    "stumped",
    "caught_keeper",
    "caught_fielder",
    "dismissals_per_inns",
}
FOW_OVERALL = {
    "fow_innings",
    "fow_notouts",
    "fow_outs",
    "fow_runs",
    "fow_average",
    "fow_balls_faced",
    "fow_run_rate",
    "fow_hundreds",
    "fow_fifty_plus",
}
TEAM_OVERALL = {
    "matches",
    "won",
    "lost",
    "tied",
    "drawn",
    "no_result",
    "win_loss_ratio",
    "percentage_won",
    "percentage_lost",
    "percentage_drawn",
    "percentage_tied",
    "percentage_no_result",
    "runs",
    "wickets",
    "balls",
    "team_average",
    "runs_per_over",
    "team_innings",
    "team_high_score",
    "team_low_score",
}
QUAL_FIELDS: dict[str, dict[str, set[str]]] = {
    "batting": BAT_BY_VIEW,
    "bowling": BOWL_BY_VIEW,
    "fielding": {view: FIELD_OVERALL for view in TYPE_VIEWS["fielding"]},
    "allround": {
        view: BAT_OVERALL | BOWL_OVERALL | FIELD_OVERALL | {"allround_average"}
        for view in TYPE_VIEWS["allround"]
    },
    "fow": {view: FOW_OVERALL for view in TYPE_VIEWS["fow"]},
    "team": {view: TEAM_OVERALL for view in TYPE_VIEWS["team"]},
    "aggregate": {
        view: TEAM_OVERALL
        - {"lost", "win_loss_ratio", "team_innings", "team_high_score", "team_low_score"}
        for view in TYPE_VIEWS["aggregate"]
    },
}
SORT_EXTRAS: dict[str, dict[str, set[str]]] = {
    "batting": {
        "": {"player", "start", "high_score"},
        "ground": {"player", "start", "high_score"},
        "host": {"player", "start", "high_score"},
        "opposition": {"player", "start", "high_score"},
        "series": {"player", "start", "high_score"},
        "year": {"player", "high_score"},
        "season": {"player", "high_score"},
        "innings": {"player", "start", "age", "batting_position", "dismissal", "innings_number"},
        "match": {"player", "start", "age", "batting_score1", "batting_score2"},
    },
    "bowling": {
        "": {"player", "start", "bbi", "bbm"},
        "ground": {"player", "start", "bbi", "bbm"},
        "host": {"player", "start", "bbi", "bbm"},
        "opposition": {"player", "start", "bbi", "bbm"},
        "series": {"player", "start", "bbi", "bbm"},
        "year": {"player", "bbi", "bbm"},
        "season": {"player", "bbi", "bbm"},
        "innings": {"player", "start", "age"},
        "match": {"player", "start", "age", "bbi"},
    },
    "fielding": {
        view: {"player", "start", "age", "max_dismissals"} for view in TYPE_VIEWS["fielding"]
    },
    "allround": {
        view: {"player", "start", "high_score", "bbi", "bbm", "max_dismissals"}
        for view in TYPE_VIEWS["allround"]
    },
    "fow": {view: {"partners", "start", "fow_high_score"} for view in TYPE_VIEWS["fow"]},
    "team": {view: {"team", "start"} for view in TYPE_VIEWS["team"]},
    "aggregate": {view: {"start"} for view in TYPE_VIEWS["aggregate"]},
}

MULTI_VALUE_LABELS = {
    "result": {"1": "won", "2": "lost", "3": "tied", "4": "drawn", "5": "no result"},
    "home_or_away": {"1": "home", "2": "away", "3": "neutral"},
}

CHOICE_VALUES: dict[str, set[str]] = {
    "home_or_away": {"1", "2", "3"},
    "result": {"1", "2", "3", "4"},
    "tournament_type": {"2", "3", "5"},
    "final_type": {"0", "1"},
    "floodlit": {"1", "2"},
    "toss": {"1", "2"},
    "batting_fielding_first": {"1", "2"},
    "captain": {"0", "1"},
    "keeper": {"0", "1"},
    "debut_or_last": {"1", "2", "3", "4"},
    "batting_hand": {"1", "2"},
    "bowling_hand": {"1", "2", "3"},
    "bowling_pacespin": {"1", "2", "3"},
    "innings_number": {"1", "2", "3", "4"},
    "outs": {"0", "1"},
    "dismissal": {"1", "2", "3", "4", "5", "6", "7", "8", "11", "12", "13"},
    "fow_type": {"1", "2", "3"},
    "event": {"1", "2", "3", "4"},
    "team_view": {"bowl"},
}

PLAYER_PAGE_CHOICE_VALUES = CHOICE_VALUES | {"result": {"1", "2", "3", "5"}}

QUICK_PICK_VALUES: dict[str, set[str]] = {
    "spanquickpick": {str(value) for value in range(1, 28)},
    "agequickpick": {str(value) for value in range(1, 7)},
    "runsquickpick": {str(value) for value in range(1, 10)},
    "batting_positionquickpick": {str(value) for value in range(1, 15)},
    "ballsquickpick": {str(value) for value in range(1, 8)},
    "concededquickpick": {str(value) for value in range(1, 8)},
    "wicketsquickpick": {str(value) for value in range(1, 6)},
    "bowling_positionquickpick": {str(value) for value in range(1, 5)},
    "caughtquickpick": {str(value) for value in range(1, 8)},
    "stumpedquickpick": {str(value) for value in range(1, 6)},
}

RANGE_LIMITS: dict[str, tuple[int, int]] = {
    "agemin1": (14, 52),
    "agemax1": (14, 52),
    "runsmin1": (0, 952),
    "runsmax1": (0, 952),
    "batting_positionmin1": (0, 12),
    "batting_positionmax1": (0, 12),
    "ballsmin1": (0, 2012),
    "ballsmax1": (0, 2012),
    "concededmin1": (0, 298),
    "concededmax1": (0, 298),
    "wicketsmin1": (0, 10),
    "wicketsmax1": (0, 10),
    "bowling_positionmin1": (0, 11),
    "bowling_positionmax1": (0, 11),
    "caughtmin1": (0, 7),
    "caughtmax1": (0, 7),
    "stumpedmin1": (0, 5),
    "stumpedmax1": (0, 5),
    "partnership_runsmin1": (0, 624),
    "partnership_runsmax1": (0, 624),
    "partnership_wicketmin1": (1, 10),
    "partnership_wicketmax1": (1, 10),
}

Value = str | int | Decimal | date
ValueList = Annotated[Value | Sequence[Value], Field(union_mode="left_to_right")]


class QuerySpecError(ValueError):
    """Raised when a Statsguru query cannot be compiled."""


class SymbolicPeriodKind(StrEnum):
    CAREER = "career"
    FIRST_YEARS = "first_years"
    LAST_YEARS = "last_years"


class ResolvedPeriod(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: date
    end: date

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.start > self.end:
            raise ValueError("period start date must be on or before end date")
        return self


class SeasonPeriod(BaseModel):
    model_config = ConfigDict(frozen=True)

    season: str


class SymbolicPeriod(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: SymbolicPeriodKind
    years: PositiveInt | None = None

    @model_validator(mode="after")
    def validate_years(self) -> Self:
        if self.kind == SymbolicPeriodKind.CAREER and self.years is not None:
            raise ValueError("career period must not include years")
        if self.kind != SymbolicPeriodKind.CAREER and self.years is None:
            raise ValueError(f"{self.kind.value} period requires years")
        return self

    def resolve(self, first_match: date, last_match: date) -> ResolvedPeriod:
        if first_match > last_match:
            raise QuerySpecError("career first match date must be on or before last match date")
        if self.kind == SymbolicPeriodKind.CAREER:
            return ResolvedPeriod(start=first_match, end=last_match)
        assert self.years is not None
        if self.kind == SymbolicPeriodKind.FIRST_YEARS:
            return ResolvedPeriod(start=first_match, end=_add_years(first_match, self.years))
        return ResolvedPeriod(start=_add_years(last_match, -self.years), end=last_match)


Period = ResolvedPeriod | SeasonPeriod | SymbolicPeriod | None


class Qualification(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    minimum: Decimal | int | None = None
    maximum: Decimal | int | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.minimum is None and self.maximum is None:
            raise ValueError("qualification requires a minimum or maximum")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError(f"qualification {self.field} minimum must be <= maximum")
        return self


class StatsguruQuery(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    class_: int = Field(alias="class")
    type: StatType
    view: View = ""
    groupby: str | None = None
    period: Period = None
    qualifications: tuple[Qualification, ...] = ()
    orderby: str | None = None
    orderbyad: Literal["", "reverse"] = ""
    size: Literal[10, 25, 50, 100, 150, 200] = 50
    page: PositiveInt | None = None

    team: ValueList | None = None
    opposition: ValueList | None = None
    home_or_away: ValueList | None = None
    host: ValueList | None = None
    ground: ValueList | None = None
    spanquickpick: int | None = None
    season: ValueList | None = None
    result: ValueList | None = None
    continent: ValueList | None = None
    series: ValueList | None = None
    trophy: ValueList | None = None
    tournament_type: ValueList | None = None
    final_type: ValueList | None = None
    floodlit: ValueList | None = None
    toss: ValueList | None = None
    batting_fielding_first: ValueList | None = None
    captain: ValueList | None = None
    keeper: ValueList | None = None
    debut_or_last: ValueList | None = None
    batting_hand: ValueList | None = None
    bowling_hand: ValueList | None = None
    bowling_pacespin: ValueList | None = None
    search_player: str | None = None
    player_involve: ValueList | None = None
    player_involve_type: str | None = None
    search_captain: str | None = None
    captain_involve: ValueList | None = None
    captain_involve_type: str | None = None

    agemin1: int | None = None
    agemax1: int | None = None
    agequickpick: int | None = None
    innings_number: ValueList | None = None
    runsmin1: int | None = None
    runsmax1: int | None = None
    runsquickpick: int | None = None
    batting_positionmin1: int | None = None
    batting_positionmax1: int | None = None
    batting_positionquickpick: int | None = None
    outs: ValueList | None = None
    dismissal: ValueList | None = None
    ballsmin1: int | None = None
    ballsmax1: int | None = None
    ballsquickpick: int | None = None
    concededmin1: int | None = None
    concededmax1: int | None = None
    concededquickpick: int | None = None
    wicketsmin1: int | None = None
    wicketsmax1: int | None = None
    wicketsquickpick: int | None = None
    bowling_positionmin1: int | None = None
    bowling_positionmax1: int | None = None
    bowling_positionquickpick: int | None = None
    caughtmin1: int | None = None
    caughtmax1: int | None = None
    caughtquickpick: int | None = None
    stumpedmin1: int | None = None
    stumpedmax1: int | None = None
    stumpedquickpick: int | None = None
    partnership_runsmin1: int | None = None
    partnership_runsmax1: int | None = None
    partnership_runsquickpick: int | None = None
    partnership_wicketmin1: int | None = None
    partnership_wicketmax1: int | None = None
    partnership_wicketquickpick: int | None = None
    fow_type: ValueList | None = None
    event: ValueList | None = None
    team_view: str | None = None
    qualquickpick: int | None = None

    @model_validator(mode="after")
    def validate_query(self) -> Self:
        if self.class_ not in id_tables.CLASS_IDS:
            raise ValueError(f"unknown class {self.class_}; expected one of {id_tables.CLASS_IDS}")
        if self.view not in TYPE_VIEWS[self.type]:
            raise ValueError(f"view {self.view!r} is not valid for type {self.type!r}")
        if self.groupby and self.type == "aggregate":
            raise ValueError("field 'groupby' is not valid for type 'aggregate'")
        _validate_filters_for_type(self)
        _validate_choice_values(self, CHOICE_VALUES)
        _validate_ranges(self)
        _validate_built_in_ids(self)
        _validate_qualifications_and_sort(self)
        if len(self.qualifications) > 3:
            raise ValueError("Statsguru supports at most three result qualifications")
        if isinstance(self.period, SeasonPeriod) and self.season is not None:
            raise ValueError("period season and season filter cannot both be set")
        return self

    def resolved(self, *, first_match: date, last_match: date) -> Self:
        if not isinstance(self.period, SymbolicPeriod):
            return self
        return self.model_copy(update={"period": self.period.resolve(first_match, last_match)})

    def results_url(self, *, as_of: date | None = None) -> str:
        params = self._params(as_of=as_of or date.today(), include_template=True)
        return _build_url(f"{STATS_BASE}/stats/index.html", params)

    def label(self, *, as_of: date | None = None) -> str:
        pieces = [f"{CLASS_LABELS[self.class_]} {TYPE_LABELS[self.type]}"]
        filters = _label_filters(self)
        if filters:
            pieces.append(", ".join(filters))
        if self.qualifications:
            pieces.extend(_label_qualification(q) for q in self.qualifications)
        period_label = _label_period(self._effective_period(as_of=as_of or date.today()))
        if period_label:
            pieces.append(period_label)
        if self.orderby:
            direction = "ascending" if self.orderbyad == "reverse" else "descending"
            pieces.append(f"sorted by {_field_label(self.orderby)} {direction}")
        return ", ".join(pieces)

    def _params(self, *, as_of: date, include_template: bool) -> list[tuple[str, str]]:
        if isinstance(self.period, SymbolicPeriod):
            raise QuerySpecError(
                f"period {self.period.kind.value!r} must be resolved before compiling"
            )
        params: list[tuple[str, str]] = [("class", str(self.class_)), ("type", self.type)]
        if include_template:
            params.append(("template", "results"))
        if self.view:
            params.append(("view", self.view))
        if self.groupby:
            params.append(("groupby", self.groupby))
        params.extend(_filter_params(self))
        period = self._effective_period(as_of=as_of)
        params.extend(_period_params(period))
        for index, qualification in enumerate(self.qualifications, start=1):
            params.append((f"qualval{index}", qualification.field))
            if qualification.minimum is not None:
                params.append((f"qualmin{index}", _number(qualification.minimum)))
            if qualification.maximum is not None:
                params.append((f"qualmax{index}", _number(qualification.maximum)))
        if self.orderby:
            params.append(("orderby", self.orderby))
        if self.orderbyad:
            params.append(("orderbyad", self.orderbyad))
        if self.size != 50:
            params.append(("size", str(self.size)))
        if self.page is not None:
            params.append(("page", str(self.page)))
        return params

    def _effective_period(self, *, as_of: date) -> ResolvedPeriod | SeasonPeriod:
        if isinstance(self.period, ResolvedPeriod | SeasonPeriod):
            return self.period
        if isinstance(self.period, SymbolicPeriod):
            raise QuerySpecError(
                f"period {self.period.kind.value!r} must be resolved before compiling"
            )
        return ResolvedPeriod(
            start=_parse_table_date(id_tables.FIRST_MATCH_DATES[self.class_]), end=as_of
        )


class PlayerPageSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    player_id: PositiveInt
    class_: int = Field(alias="class")
    type: Literal["allround", "batting", "bowling", "fielding"]
    view: str = ""
    period: Period = None
    opposition: ValueList | None = None
    host: ValueList | None = None
    ground: ValueList | None = None
    home_or_away: ValueList | None = None
    spanquickpick: int | None = None
    season: ValueList | None = None
    result: ValueList | None = None
    trophy: ValueList | None = None

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        if self.class_ not in id_tables.CLASS_IDS:
            raise ValueError(f"unknown class {self.class_}; expected one of {id_tables.CLASS_IDS}")
        _validate_choice_values(self, PLAYER_PAGE_CHOICE_VALUES)
        _validate_built_in_ids(self)
        if isinstance(self.period, SeasonPeriod) and self.season is not None:
            raise ValueError("period season and season filter cannot both be set")
        return self

    def resolved(self, *, first_match: date, last_match: date) -> Self:
        if not isinstance(self.period, SymbolicPeriod):
            return self
        return self.model_copy(update={"period": self.period.resolve(first_match, last_match)})

    def url(self, *, as_of: date | None = None) -> str:
        if isinstance(self.period, SymbolicPeriod):
            raise QuerySpecError(
                f"period {self.period.kind.value!r} must be resolved before compiling"
            )
        params: list[tuple[str, str]] = [
            ("class", str(self.class_)),
            ("template", "results"),
            ("type", self.type),
        ]
        if self.view:
            params.append(("view", self.view))
        params.extend(_filter_params(self))
        period = self._effective_period(as_of=as_of or date.today())
        params.extend(_period_params(period))
        return _build_url(f"{STATS_BASE}/player/{self.player_id}.html", params)

    def _effective_period(self, *, as_of: date) -> ResolvedPeriod | SeasonPeriod:
        if isinstance(self.period, ResolvedPeriod | SeasonPeriod):
            return self.period
        return ResolvedPeriod(
            start=_parse_table_date(id_tables.FIRST_MATCH_DATES[self.class_]), end=as_of
        )


@dataclass(frozen=True)
class PlayerSearchSpec:
    search: str

    def url(self) -> str:
        return _build_url(
            f"{STATS_BASE}/stats/analysis.html",
            [("search", self.search), ("template", "analysis")],
        )


def player_search_url(search: str) -> str:
    return PlayerSearchSpec(search).url()


def _validate_filters_for_type(query: StatsguruQuery) -> None:
    allowed = TYPE_FILTER_FIELDS[query.type]
    provided = set(_provided_filter_values(query))
    invalid = sorted(provided - allowed)
    if invalid:
        raise ValueError(f"field {invalid[0]!r} is not valid for type {query.type!r}")


def _provided_filter_values(model: BaseModel) -> Iterable[str]:
    for name, value in model:
        if name in {
            "class_",
            "type",
            "view",
            "groupby",
            "period",
            "qualifications",
            "orderby",
            "orderbyad",
            "size",
            "page",
            "player_id",
        }:
            continue
        if value is not None and value != "" and value != () and value != []:
            yield name


def _validate_ranges(query: StatsguruQuery) -> None:
    pairs = (
        ("agemin1", "agemax1"),
        ("runsmin1", "runsmax1"),
        ("batting_positionmin1", "batting_positionmax1"),
        ("ballsmin1", "ballsmax1"),
        ("concededmin1", "concededmax1"),
        ("wicketsmin1", "wicketsmax1"),
        ("bowling_positionmin1", "bowling_positionmax1"),
        ("caughtmin1", "caughtmax1"),
        ("stumpedmin1", "stumpedmax1"),
        ("partnership_runsmin1", "partnership_runsmax1"),
        ("partnership_wicketmin1", "partnership_wicketmax1"),
    )
    for low_name, high_name in pairs:
        low = getattr(query, low_name)
        high = getattr(query, high_name)
        if low is not None and high is not None and low > high:
            raise ValueError(f"{low_name} must be <= {high_name}")
    for field, (low, high) in RANGE_LIMITS.items():
        value = getattr(query, field)
        if value is not None and not low <= value <= high:
            raise ValueError(f"{field} must be from {low} to {high}")


def _validate_choice_values(model: BaseModel, choices: Mapping[str, set[str]]) -> None:
    for field, allowed in choices.items():
        value = getattr(model, field, None)
        if value is None or value == "":
            continue
        for item in _as_sequence(value):
            if str(item) not in allowed:
                raise ValueError(
                    f"{field} value {item!r} is not valid; expected one of "
                    f"{', '.join(sorted(allowed))}"
                )
    for field, allowed in QUICK_PICK_VALUES.items():
        value = getattr(model, field, None)
        if value is None:
            continue
        if str(value) not in allowed:
            raise ValueError(
                f"{field} value {value!r} is not valid; expected one of "
                f"{', '.join(sorted(allowed))}"
            )


def _validate_built_in_ids(model: BaseModel) -> None:
    class_id = model.class_
    for field, table, kind in (
        ("team", id_tables.TEAMS, "team"),
        ("opposition", id_tables.TEAMS, "opposition"),
        ("host", id_tables.HOSTS, "host"),
        ("continent", id_tables.CONTINENTS, "continent"),
        ("trophy", id_tables.TROPHIES, "trophy"),
    ):
        value = getattr(model, field, None)
        if value is None:
            continue
        allowed = table[class_id]
        for item in _as_sequence(value):
            if int(item) not in allowed:
                raise ValueError(f"unknown {kind} ID {item} for class {class_id}")


def _validate_qualifications_and_sort(query: StatsguruQuery) -> None:
    qual_fields = QUAL_FIELDS[query.type].get(query.view, QUAL_FIELDS[query.type].get("", set()))
    sort_fields = qual_fields | SORT_EXTRAS[query.type].get(
        query.view, SORT_EXTRAS[query.type].get("", set())
    )
    for qualification in query.qualifications:
        if qualification.field not in qual_fields:
            raise ValueError(
                f"qualification field {qualification.field!r} is not valid for "
                f"type {query.type!r} view {query.view or 'overall'!r}"
            )
    if query.orderby and query.orderby not in sort_fields:
        raise ValueError(
            f"sort field {query.orderby!r} is not valid for type {query.type!r} "
            f"view {query.view or 'overall'!r}"
        )


def _filter_params(model: BaseModel) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    for name in _provided_filter_values(model):
        value = getattr(model, name)
        for item in _as_sequence(value):
            params.append((name, _value(item)))
    return params


def _period_params(period: ResolvedPeriod | SeasonPeriod) -> list[tuple[str, str]]:
    if isinstance(period, SeasonPeriod):
        return [("season", period.season)]
    return [
        ("spanmax1", _format_date(period.end)),
        ("spanmin1", _format_date(period.start)),
        ("spanval1", "span"),
    ]


def _as_sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, str | int | Decimal | date):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    return (value,)


def _build_url(base: str, params: Sequence[tuple[str, str]]) -> str:
    ordered = sorted(params, key=lambda item: item[0])
    query = ";".join(f"{quote_plus(key)}={quote_plus(value)}" for key, value in ordered)
    return f"{base}?{query}"


def _value(value: object) -> str:
    if isinstance(value, date):
        return _format_date(value)
    if isinstance(value, Decimal):
        return _number(value)
    return str(value)


def _number(value: Decimal | int) -> str:
    if isinstance(value, int):
        return str(value)
    normalized = value.normalize()
    return format(normalized, "f")


def _format_date(value: date) -> str:
    return value.strftime("%d %b %Y")


def _parse_table_date(value: str) -> date:
    day, month_name, year = value.split()
    month = {
        name: index
        for index, name in enumerate(
            ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
            start=1,
        )
    }[month_name]
    return date(int(year), month, int(day))


def _add_years(value: date, years: int) -> date:
    target_year = value.year + years
    try:
        return value.replace(year=target_year)
    except ValueError:
        return value.replace(year=target_year, day=28)


def _label_filters(query: StatsguruQuery) -> list[str]:
    labels = []
    for field, table, noun in (
        ("team", id_tables.TEAMS, "team"),
        ("opposition", id_tables.TEAMS, "opposition"),
        ("host", id_tables.HOSTS, "in"),
        ("continent", id_tables.CONTINENTS, "continent"),
        ("trophy", id_tables.TROPHIES, "trophy"),
    ):
        value = getattr(query, field)
        if value is None:
            continue
        names = [table[query.class_][int(item)] for item in _as_sequence(value)]
        if noun == "in":
            labels.append(f"in {_join_words(names)}")
        else:
            labels.append(f"{noun} {_join_words(names)}")
    for field, names in MULTI_VALUE_LABELS.items():
        value = getattr(query, field)
        if value is None:
            continue
        selected = [names.get(str(item), str(item)) for item in _as_sequence(value)]
        labels.append(f"{field.replace('_', ' ')} {_join_words(selected)}")
    return labels


def _label_qualification(qualification: Qualification) -> str:
    label = _field_label(qualification.field)
    if qualification.minimum is not None and qualification.maximum is not None:
        return f"{label} from {_number(qualification.minimum)} to {_number(qualification.maximum)}"
    if qualification.minimum is not None:
        return f"at least {_number(qualification.minimum)} {label}"
    assert qualification.maximum is not None
    return f"at most {_number(qualification.maximum)} {label}"


def _label_period(period: ResolvedPeriod | SeasonPeriod) -> str:
    if isinstance(period, SeasonPeriod):
        return f"season {period.season}"
    return f"{_label_date(period.start)} to {_label_date(period.end)}"


def _field_label(field: str) -> str:
    return FIELD_LABELS.get(field, field.replace("_", " "))


def _join_words(values: Sequence[str]) -> str:
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + f" and {values[-1]}"


def _label_date(value: date) -> str:
    return f"{value.day} {value.strftime('%b %Y')}"


__all__ = [
    "PlayerPageSpec",
    "PlayerSearchSpec",
    "Qualification",
    "QuerySpecError",
    "ResolvedPeriod",
    "SeasonPeriod",
    "StatsguruQuery",
    "SymbolicPeriod",
    "SymbolicPeriodKind",
    "player_search_url",
]
