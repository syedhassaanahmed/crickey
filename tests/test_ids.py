from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from crickey.fetcher import Fetcher, MemoryPageSource
from crickey.ids import (
    FORM_URL,
    LookupStatus,
    StatsguruIdResolver,
    lookup_continent,
    lookup_team,
    lookup_trophy,
    resolve_name,
)
from crickey.settings import Settings

_GEN_IDS_SPEC = importlib.util.spec_from_file_location(
    "gen_ids_for_tests", Path(__file__).parents[1] / "scripts" / "gen_ids.py"
)
assert _GEN_IDS_SPEC is not None and _GEN_IDS_SPEC.loader is not None
_GEN_IDS_MODULE = importlib.util.module_from_spec(_GEN_IDS_SPEC)
sys.modules[_GEN_IDS_SPEC.name] = _GEN_IDS_MODULE
_GEN_IDS_SPEC.loader.exec_module(_GEN_IDS_MODULE)
CLASS_IDS = _GEN_IDS_MODULE.CLASS_IDS
module_text_from_html_by_class = _GEN_IDS_MODULE.module_text_from_html_by_class


class FakeClock:
    def __init__(self) -> None:
        self.monotonic_time = 0.0
        self.wall_time = datetime(2026, 10, 4, tzinfo=UTC)
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.monotonic_time

    def now(self) -> datetime:
        return self.wall_time + timedelta(seconds=self.monotonic_time)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic_time += seconds
        await asyncio.sleep(0)


def run(coro: Awaitable[object]) -> object:
    return asyncio.run(coro)


def settings() -> Settings:
    return Settings(min_interval=timedelta(seconds=2), max_retries=0)


def form_html(
    *,
    spanmin0: str = "01 Jan 2000",
    team_label: str = "Alpha XI",
    ground_value: int = 701,
    ground_label: str = "AAA: Alpha Ground",
    include_new_ground: bool = False,
    include_new_series: bool = False,
    extra_ground_options: str = "",
    opposition_label: str | None = None,
) -> str:
    new_ground = '<option value="702">BBB: New Ground</option>' if include_new_ground else ""
    new_series = '<option value="802">New Series, 2026</option>' if include_new_series else ""
    series_options = f'<option value="801">Alpha Cup, 2026</option>{new_series}'
    opposition_label = team_label if opposition_label is None else opposition_label
    return f"""
    <html><body><form name="gurumenu">
    <input type="hidden" name="spanmin0" value="{spanmin0}">
    <select name="team">
      <option value="">all teams</option>
      <option value="7">{team_label}</option>
      <option value="8">Beta XI</option>
    </select>
    <select name="opposition">
      <option value="">all teams</option>
      <option value="7">{opposition_label}</option>
      <option value="8">Beta XI</option>
    </select>
    <select name="host">
      <option value="">all countries</option><option value="1">Hostland</option>
    </select>
    <select name="continent">
      <option value="">all continents</option><option value="2">Asia</option>
    </select>
    <select name="trophy">
      <option value="">all tournaments</option>
      <option value="12">World Cup</option>
      <option value="117">Indian Premier League</option>
    </select>
    <select name="ground">
      <option value="">all grounds</option>
      <option value="{ground_value}">{ground_label}</option>{new_ground}{extra_ground_options}
    </select>
    <select name="series">
      <option value="">all series</option>{series_options}
    </select>
    </form></body></html>
    """


def test_generator_builds_exact_tables_from_synthetic_forms() -> None:
    html_by_class = {
        class_id: form_html(spanmin0=f"0{index} Jan 2000", team_label=f"Class {class_id} XI")
        for index, class_id in enumerate(CLASS_IDS, start=1)
    }

    text = module_text_from_html_by_class(html_by_class, generated_on=datetime(2026, 10, 4).date())
    namespace: dict[str, object] = {}
    exec(text, namespace)

    assert namespace["CLASS_IDS"] == CLASS_IDS
    assert namespace["FIRST_MATCH_DATES"] == {
        1: "01 Jan 2000",
        2: "02 Jan 2000",
        3: "03 Jan 2000",
        11: "04 Jan 2000",
        6: "05 Jan 2000",
    }
    assert namespace["TEAMS"][3] == {7: "Class 3 XI", 8: "Beta XI"}
    assert namespace["HOSTS"][6] == {1: "Hostland"}
    assert namespace["CONTINENTS"][1] == {2: "Asia"}
    assert namespace["TROPHIES"][2] == {12: "World Cup", 117: "Indian Premier League"}


def test_generator_refuses_an_opposition_list_unlike_the_team_list() -> None:
    html_by_class = {
        class_id: form_html(spanmin0=f"0{index} Jan 2000", team_label=f"Class {class_id} XI")
        for index, class_id in enumerate(CLASS_IDS, start=1)
    }
    html_by_class[3] = form_html(
        spanmin0="03 Jan 2000", team_label="Class 3 XI", opposition_label="Other XI"
    )

    with pytest.raises(ValueError, match="class 3's opposition list differs from its team list"):
        module_text_from_html_by_class(html_by_class, generated_on=datetime(2026, 10, 4).date())


def test_builtin_lookup_tiers_resolve_acronyms_and_generic_names() -> None:
    expected_matches = (
        (lookup_trophy, 6, "IPL", 117),
        (lookup_trophy, 6, "PSL", 205),
        (lookup_team, 3, "USA", 11),
        (lookup_team, 1, "SA", 3),
        (lookup_trophy, 2, "Cricket World Cup", 12),
        (lookup_trophy, 2, "ICC Cricket World Cup", 12),
        (lookup_trophy, 3, "T20 World Cup", 89),
        (lookup_trophy, 11, "T20 World Cup", 89),
        (lookup_trophy, 2, "ODI World Cup", 12),
        (lookup_trophy, 2, "ODI World Cups", 12),
        (lookup_trophy, 2, "One-Day World Cup", 12),
        (lookup_trophy, 3, "T20I World Cup", 89),
        (lookup_trophy, 1, "Ashes Tests", 1),
        (lookup_trophy, 2, "World Cup ODIs", 12),
        (lookup_trophy, 3, "T20Is World Cup", 89),
    )

    for lookup, class_id, query, value in expected_matches:
        result = lookup(class_id, query)
        assert result.status == LookupStatus.MATCH
        assert result.match.value == value


def test_builtin_lookup_returns_candidates_for_collisions_and_unsafe_fuzzy_matches() -> None:
    big_bash = lookup_trophy(6, "Big Bash")
    team_sa = lookup_team(6, "SA")
    premier_league = lookup_trophy(6, "Premier League")
    australasia = lookup_continent(1, "Australasia")

    assert [candidate.value for candidate in big_bash.candidates] == [158, 124]
    assert {candidate.value for candidate in team_sa.candidates} == {3, 154, 571}
    assert premier_league.needs_clarification is True
    assert premier_league.match is None
    assert australasia.needs_clarification is True
    assert australasia.match is None
    assert lookup_trophy(3, "World T20").candidates[0].value == 89
    assert lookup_trophy(3, "ICC World T20").candidates[0].value == 89
    assert lookup_trophy(11, "Asia Cup").status == LookupStatus.NEEDS_CLARIFICATION
    assert 951 in {candidate.value for candidate in lookup_trophy(11, "Asia Cup").candidates}
    assert 5799 in {candidate.value for candidate in lookup_team(6, "Lahore").candidates}
    assert 7224 in {candidate.value for candidate in lookup_team(6, "Dhaka").candidates}


def test_builtin_lookup_prefix_tier_resolves_unique_team_prefixes_only() -> None:
    assert lookup_team(2, "Aus").match.value == 2
    assert lookup_team(2, "Eng").match.value == 1
    assert lookup_team(2, "Pak").match.value == 7
    assert lookup_team(2, "Au").match is None

    ind = lookup_team(2, "Ind")
    sou = lookup_team(6, "Sou")

    assert ind.match is None
    assert [candidate.value for candidate in ind.candidates] == [6, 4]
    assert len(sou.candidates) == 5
    assert [candidate.score for candidate in sou.candidates] == sorted(
        (candidate.score for candidate in sou.candidates), reverse=True
    )


def test_name_lookup_exact_case_insensitive_and_fuzzy_matches() -> None:
    exact = lookup_team(2, "  pAKisTan ")
    fuzzy = lookup_team(2, "Pakstan")
    inside_word_typo = lookup_team(1, "South Afriica")
    franchise_typo = lookup_team(6, "lahore qalanders")
    split_word_typo = lookup_team(1, "Bangla desh")
    merged_sri_lanka = lookup_team(1, "Srilanka")
    merged_south_africa = lookup_team(1, "SouthAfrica")

    assert exact.status == LookupStatus.MATCH
    assert exact.match.value == 7
    assert fuzzy.status == LookupStatus.MATCH
    assert fuzzy.match.value == 7
    assert inside_word_typo.status == LookupStatus.MATCH
    assert inside_word_typo.match.value == 3
    assert franchise_typo.status == LookupStatus.MATCH
    assert franchise_typo.match.value == 5799
    assert split_word_typo.status == LookupStatus.MATCH
    assert split_word_typo.match.value == 25
    assert merged_sri_lanka.status == LookupStatus.MATCH
    assert merged_sri_lanka.match.value == 8
    assert merged_south_africa.status == LookupStatus.MATCH
    assert merged_south_africa.match.value == 3


def test_fuzzy_match_rejects_swapped_womens_names() -> None:
    unsafe_matches = (
        (lookup_trophy, 3, "ICC Women's T20 World Cup", 89),
        (lookup_trophy, 6, "ICC Women's T20 World Cup", 89),
        (lookup_trophy, 11, "ICC Women's T20 World Cup", 89),
        (lookup_trophy, 11, "Women's T20 Asia Cup", 951),
        (lookup_trophy, 6, "The Hundred Women's Competition", 826),
        (lookup_team, 6, "Welsh Fire Women", 6677),
    )

    for lookup, class_id, name, value in unsafe_matches:
        result = lookup(class_id, name)
        assert result.needs_clarification is True
        assert result.match is None
        assert value in {candidate.value for candidate in result.candidates}


def test_fuzzy_match_allows_two_edits_only_for_long_words() -> None:
    two_edits = resolve_name(1, "trophy", "Alpha Cmpionship", {1: "Alpha Championship"})
    three_edits = resolve_name(1, "trophy", "Alpha Cmionship", {1: "Alpha Championship"})

    assert two_edits.status == LookupStatus.MATCH
    assert two_edits.match.value == 1
    assert three_edits.needs_clarification is True
    assert three_edits.match is None


def test_fuzzy_match_requires_clear_ratio_margin() -> None:
    result = resolve_name(1, "team", "Alpha Teem", {1: "Alpha Team", 2: "Alpha Teim"})

    assert result.needs_clarification is True
    assert result.match is None
    assert [candidate.value for candidate in result.candidates] == [1, 2]


def test_containment_resolves_only_when_unique_and_fuzzy_singletons_do_not() -> None:
    containment = resolve_name(1, "ground", "Eden Gardens", {1: "IND: Eden Gardens, Kolkata"})
    fuzzy = lookup_team(2, "India A")
    wrong_order = resolve_name(1, "trophy", "Bar Foo", {1: "Foo Bar Baz"})
    gap = resolve_name(1, "trophy", "Foo Baz", {1: "Foo Bar Baz"})

    assert containment.status == LookupStatus.MATCH
    assert containment.match.value == 1
    assert fuzzy.match is None
    assert wrong_order.needs_clarification is True
    assert wrong_order.match is None
    assert gap.needs_clarification is True
    assert gap.match is None


def test_fuzzy_candidates_keep_score_filter() -> None:
    result = resolve_name(
        1,
        "team",
        "Alpha T",
        {
            1: "Alpha Tigers",
            2: "Alpha Lions",
            3: "Beta Bears",
            4: "Gamma Goats",
            5: "Delta Ducks",
            6: "Echo Eagles",
        },
    )

    assert [candidate.value for candidate in result.candidates] == [1, 2]


def test_name_lookup_returns_candidates_for_ambiguous_and_unknown_names() -> None:
    ambiguous = resolve_name(1, "team", "alpha cc", {1: "Alpha CC", 2: "Alpha-CC"})
    unknown = resolve_name(1, "team", "zzzzzz", {7: "Pakistan", 6: "India", 2: "Australia"})

    assert ambiguous.needs_clarification is True
    assert [candidate.value for candidate in ambiguous.candidates] == [1, 2]
    assert unknown.needs_clarification is True
    assert 1 <= len(unknown.candidates) <= 3
    assert unknown.match is None


def test_on_demand_lookup_miss_refetches_once() -> None:
    class_id = 3
    form_url = FORM_URL.format(class_id=class_id)
    source = MemoryPageSource({form_url: form_html()})
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)
    resolver = StatsguruIdResolver(fetcher, budget=20)

    warm = run(resolver.lookup_ground(class_id, "Alpha Ground"))
    source.pages[form_url] = form_html(include_new_ground=True)
    result = run(resolver.lookup_ground(class_id, "New Ground"))

    assert warm.match.value == 701
    assert result.match.value == 702
    assert source.requests == [form_url, form_url]


def test_ground_digits_must_match_exactly_for_fuzzy_typos() -> None:
    digit = resolve_name(1, "ground", "Oval 1", {1: "AAA: Oval 2"})
    short_word = resolve_name(
        3,
        "trophy",
        "ICC Men's T20 World Cup Sub Regional Europe Qualifier B",
        {1: "ICC Men's T20 World Cup Sub Regional Europe Qualifier A"},
    )

    assert digit.needs_clarification is True
    assert digit.match is None
    assert short_word.needs_clarification is True
    assert short_word.match is None


def test_on_demand_unique_containment_match_uses_cache_without_refetch() -> None:
    class_id = 3
    form_url = FORM_URL.format(class_id=class_id)
    source = MemoryPageSource({form_url: form_html(ground_label="IND: Eden Gardens, Kolkata")})
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)
    resolver = StatsguruIdResolver(fetcher, budget=20)

    first = run(resolver.lookup_ground(class_id, "Eden Gardens"))
    second = run(resolver.lookup_ground(class_id, "Eden Gardens"))

    assert first.match.value == 701
    assert second.match.value == 701
    assert source.requests == [form_url]


def test_on_demand_cached_ordered_containment_with_gap_does_not_refetch() -> None:
    class_id = 3
    form_url = FORM_URL.format(class_id=class_id)
    source = MemoryPageSource(
        {
            form_url: form_html(
                ground_label="UAE: Dubai International Cricket Stadium",
            )
        }
    )
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)
    resolver = StatsguruIdResolver(fetcher, budget=20)

    first = run(resolver.lookup_ground(class_id, "Dubai International Stadium"))
    second = run(resolver.lookup_ground(class_id, "Dubai International Stadium"))

    assert first.needs_clarification is True
    assert first.match is None
    assert [candidate.value for candidate in first.candidates] == [701]
    assert second.needs_clarification is True
    assert second.match is None
    assert [candidate.value for candidate in second.candidates] == [701]
    assert source.requests == [form_url]


def test_on_demand_cached_ambiguous_acronym_does_not_refetch() -> None:
    class_id = 3
    form_url = FORM_URL.format(class_id=class_id)
    source = MemoryPageSource(
        {
            form_url: form_html(
                ground_label="AUS: Melbourne Cricket Ground",
                extra_ground_options=('<option value="702">NEP: Mulpani Cricket Ground</option>'),
            )
        }
    )
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)
    resolver = StatsguruIdResolver(fetcher, budget=20)

    first = run(resolver.lookup_ground(class_id, "MCG"))
    second = run(resolver.lookup_ground(class_id, "MCG"))

    assert first.needs_clarification is True
    assert {candidate.value for candidate in first.candidates} == {701, 702}
    assert second.needs_clarification is True
    assert {candidate.value for candidate in second.candidates} == {701, 702}
    assert source.requests == [form_url]


def test_on_demand_form_url_cache_miss_refetches_across_fields() -> None:
    class_id = 3
    form_url = FORM_URL.format(class_id=class_id)
    source = MemoryPageSource({form_url: form_html()})
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)
    resolver = StatsguruIdResolver(fetcher, budget=20)

    warm = run(resolver.lookup_ground(class_id, "Alpha Ground"))
    source.pages[form_url] = form_html(include_new_series=True)
    names = run(resolver.series_names(class_id, [802]))

    assert warm.match.value == 701
    assert names == {802: "New Series, 2026"}
    assert source.requests == [form_url, form_url]


def test_on_demand_lookup_first_miss_does_not_refetch() -> None:
    class_id = 3
    form_url = FORM_URL.format(class_id=class_id)
    source = MemoryPageSource({form_url: form_html()})
    fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)
    resolver = StatsguruIdResolver(fetcher, budget=20)

    result = run(resolver.lookup_ground(class_id, "New Ground"))

    assert result.needs_clarification is True
    assert result.match is None
    assert source.requests == [form_url]


def test_on_demand_lookup_uses_lookup_freshness_not_recent_ttl() -> None:
    class_id = 3
    form_url = FORM_URL.format(class_id=class_id)
    clock = FakeClock()
    source = MemoryPageSource({form_url: form_html()})
    fetcher = Fetcher(settings(), clock=clock, page_source=source)

    warm = run(StatsguruIdResolver(fetcher, budget=20).lookup_ground(class_id, "Alpha Ground"))
    clock.monotonic_time += settings().recent_ttl.total_seconds() + 1
    cached = run(StatsguruIdResolver(fetcher, budget=20).lookup_ground(class_id, "Alpha Ground"))

    assert warm.match.value == 701
    assert cached.match.value == 701
    assert source.requests == [form_url]


def test_resolver_accepts_shared_fetch_call_and_progress_stream() -> None:
    async def scenario() -> tuple[object, object, list[tuple[float, str]], list[str]]:
        source = MemoryPageSource(
            {
                FORM_URL.format(class_id=3): form_html(),
                FORM_URL.format(class_id=2): form_html(ground_value=702),
            }
        )
        progress_events: list[tuple[float, str]] = []
        fetcher = Fetcher(settings(), clock=FakeClock(), page_source=source)
        resolver = StatsguruIdResolver(fetcher, budget=20)

        async with fetcher.call(
            budget=20, progress=lambda seconds, reason: progress_events.append((seconds, reason))
        ) as call:
            t20i = await resolver.lookup_ground(3, "Alpha Ground", call=call)
            odi = await resolver.lookup_ground(2, "Alpha Ground", call=call)
        return t20i, odi, progress_events, source.requests

    t20i, odi, progress_events, requests = run(scenario())

    assert t20i.match.value == 701
    assert odi.match.value == 702
    assert progress_events == [(2.0, "spacing requests politely")]
    assert requests == [FORM_URL.format(class_id=3), FORM_URL.format(class_id=2)]
