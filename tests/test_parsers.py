from __future__ import annotations

# ruff: noqa: E501
from datetime import date
from decimal import Decimal

import pytest

from crickey.parsers import (
    Overs,
    Score,
    Span,
    StatsguruParseError,
    parse_filter_form,
    parse_overs,
    parse_player_page,
    parse_player_search,
    parse_results_page,
    parse_score,
    parse_span,
)

RESULTS_HTML = """
<html><body>
<table class="engineTable"><caption>Overall figures</caption>
<tr><th>Player</th><th>Span</th><th>Mat</th><th>Inns</th><th>NO</th><th>Runs</th><th>HS</th><th>Ave</th><th>BF</th><th>SR</th><th>100</th><th>50</th><th>0</th><th></th></tr>
<tr class="data1"><td><a href="/ci/content/player/348144.html">Babar Azam</a> (PAK)</td><td>2016-2026</td><td>145</td><td>136</td><td>18</td><td>4596</td><td>122*</td><td>38.94</td><td>3590</td><td>128.02</td><td>3</td><td>39</td><td>10</td><td><a href="javascript:void(0)"></a></td></tr>
<tr class="data1"><td><a href="/ci/content/player/793463.html">Rashid Khan</a> (AFG/ICC)</td><td>2015-2026</td><td>118</td><td>100</td><td>12</td><td>1000</td><td>55</td><td>11.36</td><td>800</td><td>125</td><td>0</td><td>1</td><td>2</td><td></td></tr>
<tr class="data1"><td>Synthetic Player (AAA/BBB)</td><td>2026</td><td>1</td><td>1</td><td>1</td><td>-</td><td>-</td><td>-</td><td>0</td><td>-</td><td>0</td><td>0</td><td>0</td><td></td></tr>
</table>
<table><tr><td>Page <b>2</b> of <b>5</b></td><td>Showing <b>201</b> - <b>400</b> of <b>850</b></td></tr></table>
<table class="engineTable"><tr class="data2"><td><b>Statsguru includes the following current or recent Tests:</b></td></tr>
<tr class="data2"><td>Synthetic XI v Example XI at Testville, 3rd Test, Sep 9-12, 2026 [<a href="/ci/engine/match/1496584.html">Test # 2635</a>]</td></tr>
<tr class="data2"><td>Example XI v Synthetic XI at Sample Ground, 2nd Test, Dec 31-Jan 4, 2027 [<a href="/ci/engine/match/1496583.html">Test # 2634 - Live</a>]</td></tr>
<tr class="data2"><td>Month XI v Year XI at Edgecase, 1st Test, Dec 30, 2026-Jan 3, 2027 [<a href="/ci/engine/match/1496582.html">Test # 2633</a>]</td></tr>
<tr class="data2"><td>Example ODI XI v Synthetic ODI XI at New Sample, 3rd ODI, Oct 3, 2026 [<a href="/ci/engine/match/1496581.html">ODI # 5024</a>]</td></tr>
</table>
</body></html>
"""


def test_results_table_parses_rows_player_metadata_totals_and_recent_matches() -> None:
    parsed = parse_results_page(RESULTS_HTML)

    assert parsed.no_records is False
    assert parsed.totals.page == 2
    assert parsed.totals.pages == 5
    assert parsed.totals.showing_from == 201
    assert parsed.totals.showing_to == 400
    assert parsed.totals.total == 850
    assert parsed.headers == (
        "Player",
        "player_name",
        "player_id",
        "player_team_codes",
        "Span",
        "Mat",
        "Inns",
        "NO",
        "Runs",
        "Runs_not_out",
        "HS",
        "HS_not_out",
        "Ave",
        "BF",
        "SR",
        "100",
        "50",
        "0",
    )
    assert len(parsed.table) == 3
    first = parsed.table.iloc[0]
    assert first["player_name"] == "Babar Azam"
    assert first["player_id"] == 348144
    assert first["player_team_codes"] == ("PAK",)
    assert first["Span"] == Span("2016", "2026")
    assert first["HS"] == 122
    assert first["HS_not_out"] is True
    assert first["Ave"] == Decimal("38.94")
    assert first["SR"] == Decimal("128.02")
    assert parsed.table.iloc[1]["player_team_codes"] == ("AFG", "ICC")
    assert parsed.table.iloc[2]["player_id"] is None
    assert parsed.table.iloc[2]["Runs"] is None
    assert parsed.table.iloc[2]["Ave"] is None
    assert parsed.table.iloc[2]["SR"] is None
    assert parsed.table.iloc[2]["Runs_not_out"] is False
    first_recent = parsed.current_or_recent_matches[0]
    assert first_recent.name == "Synthetic XI v Example XI at Testville, 3rd Test"
    assert first_recent.label == "Test # 2635"
    assert first_recent.match_id == 1496584
    assert first_recent.start_date == date(2026, 9, 9)
    assert first_recent.end_date == date(2026, 9, 12)
    assert first_recent.date == date(2026, 9, 9)
    assert (
        parsed.current_or_recent_matches[1].name
        == "Example XI v Synthetic XI at Sample Ground, 2nd Test"
    )
    assert parsed.current_or_recent_matches[1].start_date == date(2026, 12, 31)
    assert parsed.current_or_recent_matches[1].end_date == date(2027, 1, 4)
    assert parsed.current_or_recent_matches[1].is_live is True
    assert parsed.current_or_recent_matches[2].start_date == date(2026, 12, 30)
    assert parsed.current_or_recent_matches[2].end_date == date(2027, 1, 3)
    single_date = parsed.current_or_recent_matches[3]
    assert single_date.name == "Example ODI XI v Synthetic ODI XI at New Sample, 3rd ODI"
    assert single_date.label == "ODI # 5024"
    assert single_date.match_id == 1496581
    assert single_date.start_date == date(2026, 10, 3)
    assert single_date.end_date == date(2026, 10, 3)


CLASS6_RESULTS_HTML = """
<html><body>
<table class="engineTable"><tr class="data2"><td>Navigation row, not results</td></tr></table>
<table class="engineTable"><caption>Overall figures</caption>
<tr class="headlinks"><th>Player</th><th>Span</th><th>Mat</th><th>Inns</th><th>NO</th><th>Runs</th><th>HS</th><th>Ave</th><th>100</th><th>50</th><th>0</th><th></th></tr>
<tr class="data2"><td><a href="/ci/content/player/308967.html">JC Buttler</a></td><td>2009-2026</td><td>437</td><td>420</td><td>70</td><td>12012</td><td>124</td><td>34.32</td><td>8</td><td>82</td><td>19</td><td><a href="javascript:void(0)"></a></td></tr>
<tr class="note"><td colspan="12">(Comilla Victorians, Durban's Super Giants, England, Southern Brave (Men))</td></tr>
<tr class="data2"><td><a href="/ci/content/player/379143.html">V Kohli</a></td><td>2007-2026</td><td>399</td><td>382</td><td>70</td><td>12886</td><td>122*</td><td>41.30</td><td>9</td><td>98</td><td>10</td><td></td></tr>
<tr class="data2"><td><a href="/ci/content/player/5334.html">CH Gayle</a></td><td>2005-2022</td><td>463</td><td>455</td><td>54</td><td>14562</td><td>175*</td><td>36.22</td><td>22</td><td>88</td><td>29</td><td></td></tr>
<tr class="note"><td colspan="12">(Barisal Burners, Jamaica Tallawahs, West Indies)</td></tr>
</table>
<table><tr><td>Page <b>1</b> of <b>13</b></td><td>Showing <b>1</b> - <b>10</b> of <b>121</b></td></tr></table>
</body></html>
"""


def test_class6_results_parse_data2_rows_and_note_teams() -> None:
    parsed = parse_results_page(CLASS6_RESULTS_HTML)

    assert parsed.no_records is False
    assert parsed.totals.page == 1
    assert parsed.totals.pages == 13
    assert parsed.totals.showing_from == 1
    assert parsed.totals.showing_to == 10
    assert parsed.totals.total == 121
    assert parsed.headers == (
        "Player",
        "player_name",
        "player_id",
        "player_team_codes",
        "player_team_names",
        "Span",
        "Mat",
        "Inns",
        "NO",
        "Runs",
        "Runs_not_out",
        "HS",
        "HS_not_out",
        "Ave",
        "100",
        "50",
        "0",
    )
    assert len(parsed.table) == 3
    first = parsed.table.iloc[0]
    assert first["player_name"] == "JC Buttler"
    assert first["player_id"] == 308967
    assert first["player_team_codes"] == ()
    assert first["player_team_names"] == (
        "Comilla Victorians",
        "Durban's Super Giants",
        "England",
        "Southern Brave (Men)",
    )
    assert first["Span"] == Span("2009", "2026")
    assert first["Mat"] == 437
    assert first["Runs"] == 12012
    assert first["Runs_not_out"] is False
    assert first["HS"] == 124
    assert first["HS_not_out"] is False
    assert first["Ave"] == Decimal("34.32")
    no_note = parsed.table.iloc[1]
    assert no_note["player_name"] == "V Kohli"
    assert no_note["player_id"] == 379143
    assert no_note["player_team_names"] is None
    third = parsed.table.iloc[2]
    assert third["player_name"] == "CH Gayle"
    assert third["player_id"] == 5334
    assert third["player_team_names"] == (
        "Barisal Burners",
        "Jamaica Tallawahs",
        "West Indies",
    )
    assert third["HS"] == 175
    assert third["HS_not_out"] is True


def _headlinks(headers: tuple[str, ...], orderbys: tuple[str, ...], stat_type: str) -> str:
    cells = []
    for index, (header, orderby) in enumerate(zip(headers, orderbys, strict=True)):
        attrs = []
        left_columns = 1 if stat_type == "aggregate" else 2
        if index < left_columns:
            attrs.append('class="left"')
        if header == "High" or (header == "HS" and stat_type in {"batting", "allround"}):
            attrs = ['class="padAst"']
        if header:
            attrs.append("nowrap")
            attr = f" {' '.join(attrs)}" if attrs else ""
            href = f"/ci/engine/stats/index.html?class=1;orderby={orderby};size=10;template=results;type={stat_type}"
            cells.append(
                f'<th{attr}><a href="{href}" title="sort by {orderby}" class="black-link">{header}</a></th>'
            )
        else:
            cells.append("<th></th>")
    return f'<thead><tr class="headlinks">{"".join(cells)}</tr></thead>'


def _pad_dd() -> str:
    return (
        '<td class="padDD"><a href="javascript:void(0)" '
        "onmouseover=\"menuLayers.show('engine-dd1', event); return true\" "
        'onmouseout="menuLayers.hide()"><img src="http://i.imgci.com/espncricinfo/guruInvestigate.gif" '
        'width="11" height="11" border="0" alt="investigate this query"></a></td>'
    )


def _synthetic_results_page(
    stat_type: str,
    headers: tuple[str, ...],
    orderbys: tuple[str, ...],
    row_cells: tuple[str, ...],
    *,
    total: int = 1,
) -> str:
    row_html = "".join(row_cells + (_pad_dd(),))
    return f"""
    <html><body>
    <table class="engineTable"><caption>Overall figures</caption>
    {_headlinks(headers + ("",), orderbys + ("",), stat_type)}
    <tbody><tr class="data1">{row_html}</tr></tbody>
    </table>
    <table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>1</b> of <b>{total}</b></td></tr></table>
    </body></html>
    """


@pytest.mark.parametrize(
    ("stat_type", "html", "expected_columns", "expected_values"),
    [
        (
            "batting",
            _synthetic_results_page(
                "batting",
                ("Player", "Span", "Mat", "Inns", "NO", "Runs", "HS", "Ave", "100", "50", "0"),
                (
                    "player",
                    "start",
                    "matches",
                    "innings",
                    "notouts",
                    "runs",
                    "high_score",
                    "batting_average",
                    "hundreds",
                    "fifty_plus",
                    "ducks",
                ),
                (
                    '<td class="left" nowrap><a href="/ci/content/player/1.html" class="data-link">Alpha Batter</a> (AAA)</td>',
                    '<td class="left" nowrap>2020-2026</td>',
                    "<td>5</td>",
                    "<td>4</td>",
                    "<td>1</td>",
                    "<td><b>250</b></td>",
                    '<td class="padAst">101*</td>',
                    "<td>83.33</td>",
                    "<td>1</td>",
                    "<td>2</td>",
                    "<td>0</td>",
                ),
            ),
            (
                "Player",
                "player_name",
                "player_id",
                "player_team_codes",
                "Span",
                "Mat",
                "Inns",
                "NO",
                "Runs",
                "Runs_not_out",
                "HS",
                "HS_not_out",
                "Ave",
                "100",
                "50",
                "0",
            ),
            {
                "Player": "Alpha Batter (AAA)",
                "player_name": "Alpha Batter",
                "player_id": 1,
                "player_team_codes": ("AAA",),
                "Span": Span("2020", "2026"),
                "Mat": 5,
                "Inns": 4,
                "NO": 1,
                "Runs": 250,
                "HS": 101,
                "HS_not_out": True,
                "Ave": Decimal("83.33"),
                "100": 1,
                "50": 2,
                "0": 0,
            },
        ),
        (
            "bowling",
            _synthetic_results_page(
                "bowling",
                (
                    "Player",
                    "Span",
                    "Mat",
                    "Inns",
                    "Overs",
                    "Mdns",
                    "Runs",
                    "Wkts",
                    "BBI",
                    "Ave",
                    "Econ",
                    "SR",
                    "4",
                    "5",
                ),
                (
                    "player",
                    "start",
                    "matches",
                    "innings_bowled",
                    "overs",
                    "maidens",
                    "conceded",
                    "wickets",
                    "bbi",
                    "bowling_average",
                    "economy_rate",
                    "bowling_strike_rate",
                    "four_plus_wickets",
                    "five_wickets",
                ),
                (
                    '<td class="left" nowrap><a href="/ci/content/player/2.html" class="data-link">Beta Bowler</a> (BBB)</td>',
                    '<td class="left" nowrap>2021-2026</td>',
                    "<td>6</td>",
                    "<td>6</td>",
                    "<td>24.5</td>",
                    "<td>2</td>",
                    "<td>150</td>",
                    "<td><b>9</b></td>",
                    "<td>4/22</td>",
                    "<td>16.66</td>",
                    "<td>6.04</td>",
                    "<td>16.5</td>",
                    "<td>1</td>",
                    "<td>0</td>",
                ),
            ),
            (
                "Player",
                "player_name",
                "player_id",
                "player_team_codes",
                "Span",
                "Mat",
                "Inns",
                "Overs",
                "Mdns",
                "Runs",
                "Runs_not_out",
                "Wkts",
                "BBI",
                "Ave",
                "Econ",
                "SR",
                "4",
                "5",
            ),
            {
                "Player": "Beta Bowler (BBB)",
                "player_name": "Beta Bowler",
                "player_id": 2,
                "player_team_codes": ("BBB",),
                "Overs": Overs(24, 5),
                "Mdns": 2,
                "Runs": 150,
                "Wkts": 9,
                "BBI": "4/22",
                "Ave": Decimal("16.66"),
                "Econ": Decimal("6.04"),
                "SR": Decimal("16.5"),
                "4": 1,
                "5": 0,
            },
        ),
        (
            "fielding",
            _synthetic_results_page(
                "fielding",
                ("Player", "Span", "Mat", "Inns", "Dis", "Ct", "St", "Ct Wk", "Ct Fi", "MD", "D/I"),
                (
                    "player",
                    "start",
                    "matches",
                    "innings_fielded",
                    "dismissals",
                    "caught",
                    "stumped",
                    "caught_keeper",
                    "caught_fielder",
                    "max_dismissals",
                    "dismissals_per_inns",
                ),
                (
                    '<td class="left" nowrap><a href="/ci/content/player/3.html" class="data-link">Gamma Keeper</a> (CCC)</td>',
                    '<td class="left" nowrap>2019-2026</td>',
                    "<td>7</td>",
                    "<td>10</td>",
                    "<td><b>18</b></td>",
                    "<td>15</td>",
                    "<td>3</td>",
                    "<td>12</td>",
                    "<td>3</td>",
                    "<td nowrap>5 (4ct 1st)</td>",
                    "<td>1.800</td>",
                ),
            ),
            (
                "Player",
                "player_name",
                "player_id",
                "player_team_codes",
                "Span",
                "Mat",
                "Inns",
                "Dis",
                "Ct",
                "St",
                "Ct Wk",
                "Ct Fi",
                "MD",
                "D/I",
            ),
            {
                "Player": "Gamma Keeper (CCC)",
                "player_name": "Gamma Keeper",
                "player_id": 3,
                "player_team_codes": ("CCC",),
                "Dis": 18,
                "Ct": 15,
                "St": 3,
                "Ct Wk": 12,
                "Ct Fi": 3,
                "MD": "5 (4ct 1st)",
                "D/I": Decimal("1.800"),
            },
        ),
        (
            "allround",
            _synthetic_results_page(
                "allround",
                (
                    "Player",
                    "Span",
                    "Mat",
                    "Runs",
                    "HS",
                    "Bat Av",
                    "100",
                    "Wkts",
                    "BBI",
                    "Bowl Av",
                    "5",
                    "Ct",
                    "St",
                    "Ave Diff",
                ),
                (
                    "player",
                    "start",
                    "matches",
                    "runs",
                    "high_score",
                    "batting_average",
                    "hundreds",
                    "wickets",
                    "bbi",
                    "bowling_average",
                    "five_wickets",
                    "caught",
                    "stumped",
                    "allround_average",
                ),
                (
                    '<td class="left" nowrap><a href="/ci/content/player/4.html" class="data-link">Delta Allrounder</a> (DDD)</td>',
                    '<td class="left" nowrap>2018-2026</td>',
                    "<td>8</td>",
                    "<td>400</td>",
                    '<td class="padAst">99</td>',
                    "<td>36</td>",
                    "<td>1</td>",
                    "<td>20</td>",
                    "<td>5/30</td>",
                    "<td>22.50</td>",
                    "<td>2</td>",
                    "<td>12</td>",
                    "<td>0</td>",
                    "<td><b>17.50</b></td>",
                ),
            ),
            (
                "Player",
                "player_name",
                "player_id",
                "player_team_codes",
                "Span",
                "Mat",
                "Runs",
                "Runs_not_out",
                "HS",
                "HS_not_out",
                "Bat Av",
                "100",
                "Wkts",
                "BBI",
                "Bowl Av",
                "5",
                "Ct",
                "St",
                "Ave Diff",
            ),
            {
                "Player": "Delta Allrounder (DDD)",
                "player_name": "Delta Allrounder",
                "player_id": 4,
                "player_team_codes": ("DDD",),
                "Bat Av": 36,
                "100": 1,
                "Wkts": 20,
                "Bowl Av": Decimal("22.50"),
                "5": 2,
                "Ave Diff": Decimal("17.50"),
            },
        ),
        (
            "fow",
            _synthetic_results_page(
                "fow",
                ("Partners", "Span", "Inns", "NO", "Runs", "High", "Ave", "100", "50"),
                (
                    "partners",
                    "start",
                    "fow_innings",
                    "fow_notouts",
                    "fow_runs",
                    "fow_high_score",
                    "fow_average",
                    "fow_hundreds",
                    "fow_fifty_plus",
                ),
                (
                    '<td class="left"><span style="white-space: nowrap"><a href="/ci/content/player/5.html" class="data-link">Epsilon Opener</a></span>, <span style="white-space: nowrap"><a href="/ci/content/player/6.html" class="data-link">Zeta Opener</a></span> (EEE)</td>',
                    '<td class="left" nowrap>2022-2026</td>',
                    "<td>9</td>",
                    "<td>1</td>",
                    "<td><b>700</b></td>",
                    '<td class="padAst">199</td>',
                    "<td>87.50</td>",
                    "<td>2</td>",
                    "<td>3</td>",
                ),
            ),
            ("Partners", "Span", "Inns", "NO", "Runs", "Runs_not_out", "High", "Ave", "100", "50"),
            {
                "Partners": "Epsilon Opener, Zeta Opener (EEE)",
                "Span": Span("2022", "2026"),
                "Inns": 9,
                "NO": 1,
                "Runs": 700,
                "High": 199,
                "Ave": Decimal("87.50"),
                "100": 2,
                "50": 3,
            },
        ),
        (
            "team",
            _synthetic_results_page(
                "team",
                (
                    "Team",
                    "Span",
                    "Mat",
                    "Won",
                    "Lost",
                    "Tied",
                    "NR",
                    "W/L",
                    "Ave",
                    "RPO",
                    "Inns",
                    "HS",
                    "LS",
                ),
                (
                    "team",
                    "start",
                    "matches",
                    "won",
                    "lost",
                    "tied",
                    "no_result",
                    "win_loss_ratio",
                    "team_average",
                    "runs_per_over",
                    "team_innings",
                    "team_high_score",
                    "team_low_score",
                ),
                (
                    '<td class="left" nowrap><a href="/ci/content/team/99.html" class="data-link">Example XI</a></td>',
                    '<td class="left" nowrap>2020-2026</td>',
                    "<td>10</td>",
                    "<td><b>6</b></td>",
                    "<td>3</td>",
                    "<td>0</td>",
                    "<td>1</td>",
                    "<td>2</td>",
                    "<td>31.25</td>",
                    "<td>8.10</td>",
                    "<td>10</td>",
                    "<td>250</td>",
                    "<td>90</td>",
                ),
            ),
            (
                "Team",
                "Span",
                "Mat",
                "Won",
                "Lost",
                "Tied",
                "NR",
                "W/L",
                "Ave",
                "RPO",
                "Inns",
                "HS",
                "HS_not_out",
                "LS",
            ),
            {
                "Team": "Example XI",
                "Span": Span("2020", "2026"),
                "Mat": 10,
                "Won": 6,
                "Lost": 3,
                "NR": 1,
                "W/L": 2,
                "Ave": Decimal("31.25"),
                "RPO": Decimal("8.10"),
                "HS": 250,
                "LS": 90,
            },
        ),
        (
            "aggregate",
            _synthetic_results_page(
                "aggregate",
                ("Span", "Mat", "Won", "Tied", "Draw", "Runs", "Wkts", "Balls", "Ave", "RPO"),
                (
                    "start",
                    "matches",
                    "won",
                    "tied",
                    "drawn",
                    "runs",
                    "wickets",
                    "balls",
                    "team_average",
                    "runs_per_over",
                ),
                (
                    '<td class="left" nowrap>1877-2026</td>',
                    "<td>11</td>",
                    "<td>8</td>",
                    "<td>1</td>",
                    "<td>2</td>",
                    "<td><b>3000</b></td>",
                    "<td>100</td>",
                    "<td>6500</td>",
                    "<td>30.00</td>",
                    "<td>2.76</td>",
                ),
            ),
            (
                "Span",
                "Mat",
                "Won",
                "Tied",
                "Draw",
                "Runs",
                "Runs_not_out",
                "Wkts",
                "Balls",
                "Ave",
                "RPO",
            ),
            {
                "Span": Span("1877", "2026"),
                "Mat": 11,
                "Won": 8,
                "Tied": 1,
                "Draw": 2,
                "Runs": 3000,
                "Wkts": 100,
                "Balls": 6500,
                "Ave": Decimal("30.00"),
                "RPO": Decimal("2.76"),
            },
        ),
    ],
)
def test_results_tables_parse_every_stat_type(
    stat_type: str,
    html: str,
    expected_columns: tuple[str, ...],
    expected_values: dict[str, object],
) -> None:
    parsed = parse_results_page(html)

    assert parsed.no_records is False
    assert parsed.totals.page == 1
    assert parsed.totals.pages == 1
    assert parsed.totals.showing_from == 1
    assert parsed.totals.showing_to == 1
    assert parsed.totals.total == 1
    assert parsed.headers == expected_columns
    assert len(parsed.table) == 1
    row = parsed.table.iloc[0]
    for column, value in expected_values.items():
        assert row[column] == value, stat_type
    if stat_type in {"fow", "team", "aggregate"}:
        assert "player_id" not in parsed.table.columns


def test_class6_no_records_data2_page_is_empty_and_flagged() -> None:
    html = """
    <table class="engineTable"><caption>Overall figures</caption>
    <tr class="headlinks"><th>Player</th><th>Span</th><th>Mat</th></tr>
    <tr class="data2"><td>No records available to match this query</td></tr></table>
    """

    parsed = parse_results_page(html)

    assert parsed.no_records is True
    assert parsed.table.empty


def test_no_records_table_is_empty_and_flagged() -> None:
    html = """
    <table class="engineTable"><caption>Overall figures</caption>
    <tr class="data1"><td>No records available to match this query</td></tr></table>
    """

    parsed = parse_results_page(html)

    assert parsed.no_records is True
    assert parsed.table.empty


def test_missing_results_structure_raises_clear_error() -> None:
    with pytest.raises(StatsguruParseError, match="results table is missing"):
        parse_results_page("<html><body><p>No Statsguru table</p></body></html>")

    with pytest.raises(StatsguruParseError, match="caption is missing"):
        parse_results_page('<table class="engineTable"><tr class="data1"><td>A</td></tr></table>')

    with pytest.raises(StatsguruParseError, match="header row is missing"):
        parse_results_page(
            '<table class="engineTable"><caption>Overall figures</caption><tr class="data1"><td>A</td></tr></table>'
        )

    missing_paging = """
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Player</th><th>Runs</th></tr>
    <tr class="data1"><td><a href="/ci/content/player/1.html">A</a> (AAA)</td><td>1</td></tr>
    </table>
    """
    with pytest.raises(StatsguruParseError, match="paging totals are missing"):
        parse_results_page(missing_paging)

    reworded_recent = """
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Player</th><th>Runs</th></tr>
    <tr class="data1"><td><a href="/ci/content/player/1.html">A</a> (AAA)</td><td>1</td></tr>
    </table><table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>1</b> of <b>1</b></td></tr></table>
    <table class="engineTable"><tr class="data2"><td><b>Recent fixtures:</b></td></tr>
    <tr class="data2"><td>A v B at C, 1st Test, Sep 9-12, 2026 [<a href="/ci/engine/match/1.html">Test # 1</a>]</td></tr></table>
    """
    with pytest.raises(StatsguruParseError, match="current or recent matches heading is missing"):
        parse_results_page(reworded_recent)

    bad_recent_date = """
    <table class="engineTable"><caption>Overall figures</caption>
    <tr><th>Player</th><th>Runs</th></tr>
    <tr class="data1"><td><a href="/ci/content/player/1.html">A</a> (AAA)</td><td>1</td></tr>
    </table><table><tr><td>Page <b>1</b> of <b>1</b></td><td>Showing <b>1</b> - <b>1</b> of <b>1</b></td></tr></table>
    <table class="engineTable"><tr class="data2"><td><b>Statsguru includes the following current or recent Tests:</b></td></tr>
    <tr class="data2"><td>A v B at C, 1st Test, 2026 September 9 [<a href="/ci/engine/match/1.html">Test # 1</a>]</td></tr></table>
    """
    with pytest.raises(StatsguruParseError, match="date is not recognised"):
        parse_results_page(bad_recent_date)


PLAYER_HTML = """
<html><body>
<table class="engineTable"><caption>Career averages</caption>
<tr><th></th><th>Span</th><th>Mat</th><th>Inns</th><th>NO</th><th>Runs</th><th>HS</th><th>Ave</th><th>BF</th><th>SR</th><th>100</th><th>50</th><th>0</th><th>4s</th><th>6s</th><th></th></tr>
<tr class="data1"><td>unfiltered</td><td>2016-2026</td><td>145</td><td>136</td><td>18</td><td>4596</td><td>122</td><td>38.94</td><td>3590</td><td>128.02</td><td>3</td><td>39</td><td>10</td><td>477</td><td>80</td><td><a href="/ci/content/player/348144.html">Profile</a></td></tr>
<tr class="data1"><td>filtered</td><td>2020-2021</td><td>37</td><td>32</td><td>2</td><td>1215</td><td>101*</td><td>40.5</td><td>927</td><td>131.06</td><td>1</td><td>10</td><td>1</td><td>100</td><td>20</td><td><a href="/ci/content/player/348144.html">Profile</a></td></tr>
</table>
<table class="engineTable"><caption>Innings by innings list</caption>
<tr><th>Runs</th><th>Mins</th><th>BF</th><th>4s</th><th>6s</th><th>SR</th><th>Pos</th><th>Dismissal</th><th>Inns</th><th></th><th>Opposition</th><th>Ground</th><th>Start Date</th><th></th></tr>
<tr class="data1"><td>15*</td><td>13</td><td>11</td><td>2</td><td>0</td><td>136.36</td><td>3</td><td>not out</td><td>2</td><td></td><td>v <a href="/ci/content/team/1.html">England</a></td><td><a href="/ci/content/ground/1.html">Sample Ground</a></td><td>7 Sep 2016</td><td><a href="/ci/engine/match/913663.html">T20I # 566</a></td></tr>
<tr class="data1"><td>DNB</td><td>DNB</td><td>DNB</td><td>DNB</td><td>DNB</td><td>DNB</td><td>DNB</td><td>DNB</td><td>DNB</td><td></td><td>v <a href="/ci/content/team/2.html">Example</a></td><td>Sample Ground</td><td>8 Sep 2016</td><td><a href="/ci/engine/match/913664.html">T20I # 567</a></td></tr>
</table>
</body></html>
"""


BOWLING_PLAYER_HTML = """
<table class="engineTable"><caption>Career averages</caption>
<tr><th></th><th>Span</th><th>Mat</th><th>Inns</th><th>Overs</th><th>Mdns</th><th>Runs</th><th>Wkts</th><th>BBI</th><th>Ave</th><th>Econ</th><th>SR</th><th>4</th><th>5</th></tr>
<tr class="data1"><td></td><td>2018-2026</td><td>103</td><td>103</td><td>370.3</td><td>3</td><td>2904</td><td>136</td><td>4/22</td><td>21.35</td><td>7.83</td><td>16.3</td><td>3</td><td>0</td></tr>
</table>
<table class="engineTable"><caption>Innings by innings list</caption>
<tr><th>Overs</th><th>Mdns</th><th>Runs</th><th>Wkts</th><th>Econ</th><th>Pos</th><th>Inns</th><th></th><th>Opposition</th><th>Ground</th><th>Start Date</th><th></th></tr>
<tr class="data1"><td>4.0</td><td>0</td><td>27</td><td>0</td><td>6.75</td><td>3</td><td>1</td><td></td><td>v Sample</td><td>Example</td><td>3 Apr 2018</td><td><a href="/ci/engine/match/1140071.html">T20I # 665</a></td></tr>
<tr class="data1"><td>TDNB</td><td>TDNB</td><td>TDNB</td><td>TDNB</td><td>TDNB</td><td>TDNB</td><td>TDNB</td><td></td><td>v Sample</td><td>Example</td><td>4 Apr 2018</td><td><a href="/ci/engine/match/1140072.html">T20I # 666</a></td></tr>
</table>
"""


def test_player_page_parses_filtered_career_and_batting_innings() -> None:
    parsed = parse_player_page(PLAYER_HTML)

    assert parsed.profile_id == 348144
    assert list(parsed.career_averages["Grouping"]) == ["unfiltered", "filtered"]
    assert parsed.career_averages.iloc[1]["HS"] == 101
    assert parsed.career_averages.iloc[1]["HS_not_out"] is True
    assert parsed.career_averages.iloc[0]["Ave"] == Decimal("38.94")
    assert parsed.career_averages.iloc[0]["SR"] == Decimal("128.02")
    assert parsed.innings is not None
    innings = parsed.innings.iloc[0]
    assert innings["Runs"] == 15
    assert innings["Runs_not_out"] is True
    assert innings["Start Date"] == date(2016, 9, 7)
    assert innings["Match"] == "T20I # 566"
    assert innings["match_id"] == 913663
    dnb = parsed.innings.iloc[1]
    assert dnb["Runs"] is None
    assert dnb["BF"] is None
    assert dnb["Runs_not_out"] is False


def test_player_page_parses_bowling_innings_and_overs() -> None:
    parsed = parse_player_page(BOWLING_PLAYER_HTML)

    assert parsed.career_averages.iloc[0]["Overs"] == Overs(370, 3)
    assert parsed.career_averages.iloc[0]["Econ"] == Decimal("7.83")
    assert parsed.career_averages.iloc[0]["SR"] == Decimal("16.3")
    assert parsed.innings is not None
    assert parsed.innings.iloc[0]["Overs"] == Overs(4, 0)
    assert parsed.innings.iloc[1]["Overs"] is None
    assert parsed.innings.iloc[1]["Runs"] is None
    assert parsed.innings.iloc[1]["Wkts"] is None


def test_player_page_missing_career_table_raises_clear_error() -> None:
    with pytest.raises(StatsguruParseError, match="Career averages table is missing"):
        parse_player_page('<table class="engineTable"><caption>Other</caption></table>')


SEARCH_HTML = """
<table><tr><td>Babar Azam</td><td>PAK</td><td>
<a href="/ci/engine/player/348144.html?class=1;type=allround">Test matches player</a> (2016/17 - 2026, 66 matches)
<a href="/ci/engine/player/348144.html?class=3;type=allround">Twenty20 Internationals player</a> (2016 - 2025/26, 145 matches)
<a href="/ci/engine/player/348144.html?class=11;type=allround">Combined Test, ODI and T20I player</a> (2015 - 2026)
<a href="/ci/engine/player/348144.html?class=22;type=allround">Under-19s Youth Twenty20 Internationals player</a> (2009/10, 1 match)
</td></tr>
<tr><td>Example Woman</td><td>ENG</td><td><a href="/ci/engine/player/101.html?class=10;type=allround">Women's Twenty20 Internationals player</a> (2020 - 2026, 47 matches)</td></tr>
<tr><td>Example Official</td><td>AUS</td><td><a href="/ci/engine/player/102.html?class=1;type=allround">Test matches official</a> (2010 - 2020, 5 matches)</td></tr>
<tr><td>Babar Ali (Synthetic Full)</td><td>BHM/PAK</td><td><a href="/ci/engine/player/39962.html?class=6;type=allround">Twenty20 matches player</a> (2018/19, 3 matches)</td></tr></table>
"""


def test_player_search_parses_ids_names_countries_formats_spans_and_roles() -> None:
    results = parse_player_search(SEARCH_HTML)

    assert len(results) == 4
    assert results[0].player_id == 348144
    assert results[0].display_name == "Babar Azam"
    assert results[0].country_codes == ("PAK",)
    assert results[0].formats[0].class_id == 1
    assert results[0].formats[0].role == "player"
    assert results[0].formats[0].span == Span("2016/17", "2026")
    assert results[0].formats[0].match_count == 66
    assert results[0].formats[2].match_count is None
    assert results[0].formats[3].class_id == 22
    assert results[0].formats[3].match_count == 1
    assert results[1].formats[0].class_id == 10
    assert results[2].formats[0].role == "official"
    assert results[3].full_name == "Synthetic Full"
    assert results[3].country_codes == ("BHM", "PAK")


def test_player_search_rejects_rows_without_format_links() -> None:
    with pytest.raises(StatsguruParseError, match="format links are missing"):
        parse_player_search(
            '<table><tr><td>Summary</td><td>PAK</td><td><a href="/ci/engine/player/348144.html">Profile</a></td></tr></table>'
        )


def test_filter_form_parses_all_control_kinds_and_view_lists() -> None:
    html = """
    <form name="gurumenu">
    <input type="hidden" name="spanmin0" value="15 Mar 1877"><input type="hidden" name="spanmax0" value="09 Sep 2026">
    <select name="team"><option value="">all teams</option><option value="7" selected>Pakistan</option></select>
    <input type="checkbox" name="home_or_away" value="1"> home venue
    <input type="checkbox" name="home_or_away" value="2" checked> away
    <input type="checkbox" name="blank_check" value=""> either checkbox
    <input type="radio" name="toss" value="1"> won toss
    <input type="radio" name="toss" value=""> either
    <select id="havingselect_batting_default" name="qualval1"><option value="runs">runs</option><option value="batting_average" selected>average</option></select>
    <select id="orderbyselect_batting_default" name="orderby"><option value="runs">runs</option><option value="player">player</option></select>
    <select id="havingselect_batting_match" name="qualval1"><option value="high_score">high score</option></select>
    <select id="orderbyselect_batting_match" name="orderby"><option value="start">start date</option></select>
    </form>
    """

    form = parse_filter_form(html)

    assert form.hidden_fields == {"spanmin0": "15 Mar 1877", "spanmax0": "09 Sep 2026"}
    assert form.select_lists["team"][1].label == "Pakistan"
    assert form.select_lists["team"][1].selected is True
    assert "qualval1" not in form.select_lists
    assert form.select_lists["havingselect_batting_default"][1].selected is True
    assert form.checkbox_lists["home_or_away"][1].selected is True
    assert form.checkbox_lists["blank_check"][0].label == "either checkbox"
    assert form.radio_lists["toss"][1].value == ""
    assert form.radio_lists["toss"][1].label == "either"
    assert form.minimum_lists[("batting", "default")][1].value == "batting_average"
    assert form.sort_lists[("batting", "match")][0].label == "start date"
    assert len(form.controls) == 12


def test_filter_form_requires_gurumenu_form() -> None:
    with pytest.raises(StatsguruParseError, match="gurumenu"):
        parse_filter_form(
            '<form><select name="team"><option value="7">Pakistan</option></select></form>'
        )


def test_converters_handle_spans_and_overs() -> None:
    assert parse_span("2015/16 - 2026") == Span("2015/16", "2026")
    assert parse_span("2016-2026") == Span("2016", "2026")
    assert parse_span("2026") == Span("2026")
    assert parse_overs("449.5") == Overs(449, 5)
    assert parse_overs("4") == Overs(4, 0)
    assert parse_overs("DNB") is None
    assert parse_overs("TDNB") is None
    with pytest.raises(ValueError, match="legal balls"):
        parse_overs("47.8")


def test_parse_score_remains_available_for_callers() -> None:
    assert parse_score("122*") == Score(122, not_out=True)
