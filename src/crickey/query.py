from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self
from urllib.parse import quote_plus

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from crickey import id_tables, query_catalog

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
    "caught": "catches",
    "caught_fielder": "catches as a fielder",
    "caught_keeper": "catches as a wicketkeeper",
    "stumped": "stumpings",
    "dismissals_per_inns": "dismissals per innings",
    "matches": "matches",
    "player": "player",
    "start": "start date",
    "batted_score": "innings score",
    "team_score": "team score",
    "team_wickets": "team wickets",
    "team_overs": "team overs",
    "fow_score": "partnership score",
    "fow_wicket": "wicket",
    "awards_match": "match awards",
    "year": "year",
    "season": "season",
}

TYPE_VIEWS = {
    class_id: {stat_type: set(views) for stat_type, views in by_type.items()}
    for class_id, by_type in query_catalog.TYPE_VIEWS.items()
}
TYPE_GROUPBYS = {
    class_id: {stat_type: set(groupbys) for stat_type, groupbys in by_type.items()}
    for class_id, by_type in query_catalog.TYPE_GROUPBYS.items()
}
TYPE_FILTER_FIELDS = {
    class_id: {stat_type: set(fields) for stat_type, fields in by_type.items()}
    for class_id, by_type in query_catalog.TYPE_FIELD_KINDS.items()
}
MULTI_FIELDS = {
    class_id: {stat_type: set(fields) for stat_type, fields in by_type.items()}
    for class_id, by_type in query_catalog.MULTI_FIELDS.items()
}
# Repeated keys for these dropdown filters are applied as "one of" by
# classic Statsguru. Keep this explicit list aligned with the live checks in R2.
REPEATED_KEY_SELECT_FIELDS = {
    "continent",
    "dismissal",
    "event",
    "final_type",
    "fow_type",
    "ground",
    "host",
    "opposition",
    "season",
    "series",
    "team",
    "trophy",
}
STATSGURU_QUERY_LIST_FILTER_FIELDS = {
    "batting_fielding_first",
    "batting_hand",
    "bowling_hand",
    "bowling_pacespin",
    "captain",
    "captain_involve",
    "continent",
    "debut_or_last",
    "dismissal",
    "event",
    "final_type",
    "floodlit",
    "fow_type",
    "ground",
    "home_or_away",
    "host",
    "innings_number",
    "keeper",
    "opposition",
    "outs",
    "player_involve",
    "result",
    "season",
    "series",
    "team",
    "toss",
    "tournament_type",
    "trophy",
}
for _class_id, _by_type in TYPE_FILTER_FIELDS.items():
    for _stat_type, _fields in _by_type.items():
        MULTI_FIELDS[_class_id][_stat_type].update(REPEATED_KEY_SELECT_FIELDS & _fields)
for _class_id, _by_type in TYPE_FILTER_FIELDS.items():
    for _stat_type, _fields in _by_type.items():
        if "search_player" in _fields:
            _fields.update({"player_involve", "player_involve_type"})
            MULTI_FIELDS[_class_id][_stat_type].add("player_involve")
        if "search_captain" in _fields:
            _fields.update({"captain_involve", "captain_involve_type"})
            MULTI_FIELDS[_class_id][_stat_type].add("captain_involve")
SINGLE_VALUE_LIST_FIELDS = tuple(
    sorted(
        field
        for field in STATSGURU_QUERY_LIST_FILTER_FIELDS
        if any(
            field in type_fields
            for by_type in TYPE_FILTER_FIELDS.values()
            for type_fields in by_type.values()
        )
        and all(
            field not in MULTI_FIELDS[class_id][stat_type]
            for class_id, by_type in TYPE_FILTER_FIELDS.items()
            for stat_type, type_fields in by_type.items()
            if field in type_fields
        )
    )
)
QUAL_FIELDS = {
    class_id: {
        stat_type: {view: set(fields) for view, fields in by_view.items()}
        for stat_type, by_view in by_type.items()
    }
    for class_id, by_type in query_catalog.QUAL_FIELDS.items()
}
SORT_FIELDS = {
    class_id: {
        stat_type: {view: set(fields) for view, fields in by_view.items()}
        for stat_type, by_view in by_type.items()
    }
    for class_id, by_type in query_catalog.SORT_FIELDS.items()
}
CHOICE_LABELS = query_catalog.CHOICE_VALUES
CHOICE_VALUES = {
    class_id: {
        stat_type: {field: set(labels) for field, labels in by_field.items()}
        for stat_type, by_field in by_type.items()
    }
    for class_id, by_type in CHOICE_LABELS.items()
}
PLAYER_PAGE_CHOICE_LABELS = query_catalog.PLAYER_PAGE_CHOICE_VALUES
PLAYER_PAGE_CHOICE_VALUES = {
    class_id: {
        stat_type: {field: set(labels) for field, labels in by_field.items()}
        for stat_type, by_field in by_type.items()
    }
    for class_id, by_type in PLAYER_PAGE_CHOICE_LABELS.items()
}
QUICKPICK_FIELDS = {
    class_id: {stat_type: set(fields) for stat_type, fields in by_type.items()}
    for class_id, by_type in query_catalog.QUICKPICK_FIELDS.items()
}
RANGE_VAL_FIELDS = query_catalog.RANGE_VAL_FIELDS
RANGE_LIMITS = query_catalog.RANGE_LIMITS
PLAYER_PAGE_VIEWS = {
    class_id: {stat_type: set(views) for stat_type, views in by_type.items()}
    for class_id, by_type in query_catalog.PLAYER_PAGE_VIEWS.items()
}
RANGE_LABELS = {
    "age": "age",
    "balls": "balls in an innings",
    "batting_position": "batting position",
    "bowling_position": "bowling position",
    "caught": "catches in an innings",
    "conceded": "runs conceded in an innings",
    "partnership_runs": "partnership runs",
    "partnership_wicket": "partnership wicket",
    "runs": "runs in an innings",
    "stumped": "stumpings in an innings",
    "wickets": "wickets in an innings",
}
ASCENDING_SORT_FIELDS = {"start"}

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
SEASON_RE = re.compile(r"^\d{4}(?:/\d{2})?$")

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

    @model_validator(mode="after")
    def validate_season(self) -> Self:
        if not SEASON_RE.match(self.season):
            raise ValueError("season must be YYYY or YYYY/YY")
        return self


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
            return ResolvedPeriod(
                start=first_match, end=min(_add_years(first_match, self.years), last_match)
            )
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
    qualval1: str | None = None
    qualmin1: Decimal | int | None = None
    qualmax1: Decimal | int | None = None
    qualval2: str | None = None
    qualmin2: Decimal | int | None = None
    qualmax2: Decimal | int | None = None
    qualval3: str | None = None
    qualmin3: Decimal | int | None = None
    qualmax3: Decimal | int | None = None
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
        _merge_raw_qualifications(self)
        if self.class_ not in id_tables.CLASS_IDS:
            raise ValueError(f"unknown class {self.class_}; expected one of {id_tables.CLASS_IDS}")
        if self.view not in TYPE_VIEWS[self.class_][self.type]:
            raise ValueError(f"view {self.view!r} is not valid for type {self.type!r}")
        if self.groupby and self.groupby not in TYPE_GROUPBYS[self.class_][self.type]:
            raise ValueError(f"groupby {self.groupby!r} is not valid for type {self.type!r}")
        _validate_filters_for_type(self)
        _validate_quickpicks(self)
        _validate_multi_values(self)
        _validate_choice_values(self, CHOICE_VALUES[self.class_][self.type])
        _validate_name_searches(self)
        _validate_ranges(self)
        _validate_season_values(self)
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
        if self.view:
            pieces.append(f"view {self.view.replace('_', ' ')}")
        if self.groupby:
            pieces.append(f"grouped by {self.groupby.replace('_', ' ')}")
        filters = _label_filters(self)
        if filters:
            pieces.append(", ".join(filters))
        if self.qualifications:
            pieces.extend(_label_qualification(q) for q in self.qualifications)
        period_label = _label_period(self._effective_period(as_of=as_of or date.today()))
        if period_label:
            pieces.append(period_label)
        if self.orderby:
            direction = _sort_direction(self.orderby, self.orderbyad)
            direction_text = f" {direction}" if direction else ""
            pieces.append(f"sorted by {_field_label(self.orderby)}{direction_text}")
        if self.page and self.page != 1:
            pieces.append(f"page {self.page}")
        if self.size != 50:
            pieces.append(f"{self.size} results per page")
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
        params.extend(_period_params(period, class_id=self.class_, as_of=as_of))
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
        if self.page is not None and self.page != 1:
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
    continent: ValueList | None = None
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
        if self.view not in PLAYER_PAGE_VIEWS[self.class_][self.type]:
            raise ValueError(f"view {self.view!r} is not valid for player-page type {self.type!r}")
        _validate_quickpicks(self)
        _validate_multi_values(
            self,
            default_multi={
                "continent",
                "ground",
                "home_or_away",
                "host",
                "opposition",
                "result",
                "season",
                "trophy",
            },
        )
        _validate_choice_values(self, _player_page_choice_values(self))
        _validate_season_values(self)
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
        params.extend(_period_params(period, class_id=self.class_, as_of=as_of or date.today()))
        return _build_url(f"{STATS_BASE}/player/{self.player_id}.html", params)

    def label(self, *, as_of: date | None = None) -> str:
        pieces = [
            f"{CLASS_LABELS[self.class_]} player {TYPE_LABELS[self.type]} for {self.player_id}"
        ]
        if self.view:
            pieces.append(f"view {self.view.replace('_', ' ')}")
        filters = _label_filters(self)
        if filters:
            pieces.append(", ".join(filters))
        if isinstance(self.period, SymbolicPeriod):
            raise QuerySpecError(
                f"period {self.period.kind.value!r} must be resolved before labelling"
            )
        pieces.append(_label_period(self._effective_period(as_of=as_of or date.today())))
        return ", ".join(pieces)

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
    allowed = TYPE_FILTER_FIELDS[query.class_][query.type]
    provided = set(_provided_filter_values(query))
    invalid = sorted(provided - allowed)
    if invalid:
        raise ValueError(f"field {invalid[0]!r} is not valid for type {query.type!r}")


def _merge_raw_qualifications(query: StatsguruQuery) -> None:
    raw: list[Qualification] = []
    for index in range(1, 4):
        field = getattr(query, f"qualval{index}")
        minimum = getattr(query, f"qualmin{index}")
        maximum = getattr(query, f"qualmax{index}")
        if field is None and minimum is None and maximum is None:
            continue
        if field is None:
            raise ValueError(
                f"qualval{index} is required when qualmin{index} or qualmax{index} is set"
            )
        raw.append(Qualification(field=field, minimum=minimum, maximum=maximum))
    if raw:
        if query.qualifications:
            raise ValueError("use either qualifications or qualval1/qualmin1 fields, not both")
        query.qualifications = tuple(raw)
        for index in range(1, 4):
            setattr(query, f"qualval{index}", None)
            setattr(query, f"qualmin{index}", None)
            setattr(query, f"qualmax{index}", None)


def _validate_quickpicks(model: BaseModel) -> None:
    if isinstance(model, StatsguruQuery):
        fields = QUICKPICK_FIELDS[model.class_][model.type]
    else:
        fields = {"spanquickpick"}
    for field in fields:
        if getattr(model, field, None) is not None:
            if field == "spanquickpick":
                raise ValueError("spanquickpick is not supported; use period instead")
            raise ValueError(f"{field} is not supported; use min/max fields instead")


def _validate_multi_values(model: BaseModel, *, default_multi: set[str] | None = None) -> None:
    if isinstance(model, StatsguruQuery):
        multi = MULTI_FIELDS[model.class_][model.type]
    else:
        multi = default_multi or set()
    for name in _provided_filter_values(model):
        value = getattr(model, name)
        if name not in multi and _is_sequence_value(value):
            raise ValueError(f"field {name!r} accepts only one value")


def _provided_filter_values(model: BaseModel) -> Iterable[str]:
    for name, value in model:
        if name in {
            "class_",
            "type",
            "view",
            "groupby",
            "period",
            "qualifications",
            "qualval1",
            "qualmin1",
            "qualmax1",
            "qualval2",
            "qualmin2",
            "qualmax2",
            "qualval3",
            "qualmin3",
            "qualmax3",
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
    for field, (low, high) in RANGE_LIMITS[query.class_][query.type].items():
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


def _validate_name_searches(query: StatsguruQuery) -> None:
    if query.search_player is not None and query.player_involve is None:
        raise ValueError("search_player must be resolved to player_involve IDs first")
    if query.search_captain is not None and query.captain_involve is None:
        raise ValueError("search_captain must be resolved to captain_involve IDs first")


def _validate_season_values(model: BaseModel) -> None:
    period = getattr(model, "period", None)
    if isinstance(period, SeasonPeriod):
        return
    season = getattr(model, "season", None)
    if season is None:
        return
    for item in _as_sequence(season):
        if not isinstance(item, str) or not SEASON_RE.match(item):
            raise ValueError(f"season value {item!r} is not valid; expected YYYY or YYYY/YY")


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
            try:
                item_id = int(item)
            except TypeError, ValueError:
                raise ValueError(f"{kind} ID {item!r} is not a number") from None
            if item_id not in allowed:
                raise ValueError(f"unknown {kind} ID {item} for class {class_id}")


def _validate_qualifications_and_sort(query: StatsguruQuery) -> None:
    qual_fields = QUAL_FIELDS[query.class_][query.type].get(
        query.view, QUAL_FIELDS[query.class_][query.type].get("", set())
    )
    sort_fields = SORT_FIELDS[query.class_][query.type].get(
        query.view, SORT_FIELDS[query.class_][query.type].get("", set())
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
    provided_names = set(_provided_filter_values(model))
    for name in _provided_filter_values(model):
        if (
            isinstance(model, StatsguruQuery)
            and name == "search_player"
            and getattr(model, "player_involve", None) is not None
        ):
            continue
        if (
            isinstance(model, StatsguruQuery)
            and name == "search_captain"
            and getattr(model, "captain_involve", None) is not None
        ):
            continue
        value = getattr(model, name)
        values = _as_sequence(value)
        if isinstance(model, StatsguruQuery) and name in MULTI_FIELDS[model.class_][model.type]:
            values = tuple(sorted(values, key=lambda item: str(item)))
        elif isinstance(model, PlayerPageSpec) and name in {
            "continent",
            "ground",
            "home_or_away",
            "host",
            "opposition",
            "result",
            "season",
            "trophy",
        }:
            values = tuple(sorted(values, key=lambda item: str(item)))
        for item in values:
            params.append((name, _value(item)))
    if isinstance(model, StatsguruQuery):
        for prefix, val in RANGE_VAL_FIELDS[model.class_][model.type].items():
            if f"{prefix}min1" in provided_names or f"{prefix}max1" in provided_names:
                params.append((f"{prefix}val1", val))
    return params


def _period_params(
    period: ResolvedPeriod | SeasonPeriod, *, class_id: int, as_of: date
) -> list[tuple[str, str]]:
    if isinstance(period, SeasonPeriod):
        return [("season", period.season)] + _period_params(
            ResolvedPeriod(
                start=_parse_table_date(id_tables.FIRST_MATCH_DATES[class_id]), end=as_of
            ),
            class_id=class_id,
            as_of=as_of,
        )
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


def _is_sequence_value(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray)


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
    return f"{value.day:02d} {MONTHS[value.month - 1]} {value.year:04d}"


def _parse_table_date(value: str) -> date:
    day, month_name, year = value.split()
    month = {name: index for index, name in enumerate(MONTHS, start=1)}[month_name]
    return date(int(year), month, int(day))


def _add_years(value: date, years: int) -> date:
    target_year = value.year + years
    try:
        return value.replace(year=target_year)
    except ValueError:
        return value.replace(year=target_year, day=28)


def _label_filters(model: BaseModel) -> list[str]:
    labels = []
    class_id = model.class_
    for field, table, noun in (
        ("team", id_tables.TEAMS, "team"),
        ("opposition", id_tables.TEAMS, "opposition"),
        ("host", id_tables.HOSTS, "in"),
        ("continent", id_tables.CONTINENTS, "continent"),
        ("trophy", id_tables.TROPHIES, "trophy"),
    ):
        value = getattr(model, field, None)
        if value is None:
            continue
        names = [table[class_id][int(item)] for item in sorted(_as_sequence(value), key=str)]
        if noun == "in":
            labels.append(f"in {_join_words(names, conjunction='or')}")
        else:
            labels.append(f"{noun} {_join_words(names, conjunction='or')}")
    choice_labels = {
        **(
            CHOICE_LABELS[model.class_][model.type]
            if isinstance(model, StatsguruQuery)
            else PLAYER_PAGE_CHOICE_LABELS[model.class_].get(model.type, {})
        )
    }
    for field, names in choice_labels.items():
        value = getattr(model, field, None)
        if value is None:
            continue
        selected = [
            names.get(str(item), str(item)) for item in sorted(_as_sequence(value), key=str)
        ]
        labels.append(f"{field.replace('_', ' ')} {_join_words(selected, conjunction='or')}")
    if getattr(model, "player_involve", None) is not None:
        selected = [str(item) for item in sorted(_as_sequence(model.player_involve), key=str)]
        prefix = "excluding " if getattr(model, "player_involve_type", None) == "none" else ""
        labels.append(f"{prefix}player involve {_join_words(selected, conjunction='or')}")
    if getattr(model, "captain_involve", None) is not None:
        selected = [str(item) for item in sorted(_as_sequence(model.captain_involve), key=str)]
        prefix = "excluding " if getattr(model, "captain_involve_type", None) == "none" else ""
        labels.append(f"{prefix}captain involve {_join_words(selected, conjunction='or')}")
    simple_fields = (
        "season",
        "ground",
        "series",
        "search_player",
        "search_captain",
    )
    for field in simple_fields:
        value = getattr(model, field, None)
        if value is None:
            continue
        selected = [str(item) for item in sorted(_as_sequence(value), key=str)]
        labels.append(f"{field.replace('_', ' ')} {_join_words(selected, conjunction='or')}")
    if isinstance(model, StatsguruQuery):
        labels.extend(_label_ranges(model))
    return labels


def _label_ranges(query: StatsguruQuery) -> list[str]:
    labels: list[str] = []
    for prefix in RANGE_VAL_FIELDS[query.class_][query.type]:
        low = getattr(query, f"{prefix}min1", None)
        high = getattr(query, f"{prefix}max1", None)
        if low is None and high is None:
            continue
        label = RANGE_LABELS.get(prefix, _field_label(prefix))
        if low is not None and high is not None:
            labels.append(f"{label} from {low} to {high}")
        elif low is not None:
            labels.append(f"at least {low} {label}")
        else:
            labels.append(f"at most {high} {label}")
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


def _join_words(values: Sequence[str], *, conjunction: str = "and") -> str:
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + f" {conjunction} {values[-1]}"


def _label_date(value: date) -> str:
    return f"{value.day} {MONTHS[value.month - 1]} {value.year:04d}"


def _sort_direction(field: str, orderbyad: str) -> str:
    default = "ascending" if field in ASCENDING_SORT_FIELDS else ""
    if not default:
        return "reverse order" if orderbyad == "reverse" else ""
    if orderbyad == "reverse":
        return "descending" if default == "ascending" else "ascending"
    return default


def _player_page_choice_values(model: PlayerPageSpec) -> Mapping[str, set[str]]:
    by_type = PLAYER_PAGE_CHOICE_VALUES[model.class_]
    return by_type.get(model.type, by_type.get("batting", {}))


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
