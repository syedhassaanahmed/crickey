from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RepresentativeStatQuery:
    filters: dict[str, int]
    qualification: str
    qualification_minimum: int
    orderby: str
    expected_filter_params: dict[str, str]


REPRESENTATIVE_STAT_QUERIES = {
    "batting": RepresentativeStatQuery(
        filters={"runsmin1": 1},
        qualification="runs",
        qualification_minimum=1,
        orderby="runs",
        expected_filter_params={"runsmin1": "1", "runsval1": "runs"},
    ),
    "bowling": RepresentativeStatQuery(
        filters={"wicketsmin1": 1},
        qualification="wickets",
        qualification_minimum=1,
        orderby="wickets",
        expected_filter_params={"wicketsmin1": "1", "wicketsval1": "wickets"},
    ),
    "fielding": RepresentativeStatQuery(
        filters={"caughtmin1": 1},
        qualification="dismissals",
        qualification_minimum=1,
        orderby="dismissals",
        expected_filter_params={"caughtmin1": "1", "caughtval1": "caught"},
    ),
    "allround": RepresentativeStatQuery(
        filters={"wicketsmin1": 1},
        qualification="allround_average",
        qualification_minimum=1,
        orderby="allround_average",
        expected_filter_params={"wicketsmin1": "1", "wicketsval1": "wickets"},
    ),
    "fow": RepresentativeStatQuery(
        filters={"partnership_runsmin1": 1},
        qualification="fow_runs",
        qualification_minimum=1,
        orderby="fow_runs",
        expected_filter_params={
            "partnership_runsmin1": "1",
            "partnership_runsval1": "partnership_runs",
        },
    ),
    "team": RepresentativeStatQuery(
        filters={"runsmin1": 1},
        qualification="won",
        qualification_minimum=1,
        orderby="won",
        expected_filter_params={"runsmin1": "1", "runsval1": "runs"},
    ),
    "aggregate": RepresentativeStatQuery(
        filters={},
        qualification="runs",
        qualification_minimum=1,
        orderby="runs",
        expected_filter_params={},
    ),
}


def representative_query_payload(class_id: int, stat_type: str) -> dict[str, object]:
    case = REPRESENTATIVE_STAT_QUERIES[stat_type]
    return {
        "class": class_id,
        "type": stat_type,
        **case.filters,
        "qualval1": case.qualification,
        "qualmin1": case.qualification_minimum,
        "orderby": case.orderby,
        "size": 10,
    }
