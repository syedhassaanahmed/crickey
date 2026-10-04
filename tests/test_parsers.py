from __future__ import annotations

# ruff: noqa: E501
from datetime import date
from decimal import Decimal
from fractions import Fraction

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
from crickey.parsers.convert import exact_batting_average, exact_strike_rate

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
        "exact_batting_average",
        "exact_batting_strike_rate",
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
    assert first["exact_batting_average"] == Fraction(4596, 118)
    assert first["exact_batting_strike_rate"] == Fraction(459600, 3590)
    assert parsed.table.iloc[1]["player_team_codes"] == ("AFG", "ICC")
    assert parsed.table.iloc[2]["player_id"] is None
    assert parsed.table.iloc[2]["Runs"] is None
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
    assert parsed.career_averages.iloc[0]["exact_batting_average"] == Fraction(4596, 118)
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


def test_converters_handle_spans_overs_no_innings_and_exact_undefined_values() -> None:
    assert parse_span("2015/16 - 2026") == Span("2015/16", "2026")
    assert parse_span("2016-2026") == Span("2016", "2026")
    assert parse_span("2026") == Span("2026")
    assert parse_overs("449.5") == Overs(449, 5)
    assert parse_overs("4") == Overs(4, 0)
    assert parse_overs("DNB") is None
    assert parse_overs("TDNB") is None
    assert exact_batting_average(100, 10, 10) is None
    assert exact_strike_rate(100, 0) is None
    with pytest.raises(ValueError, match="legal balls"):
        parse_overs("47.8")


def test_parse_score_remains_available_for_callers() -> None:
    assert parse_score("122*") == Score(122, not_out=True)
