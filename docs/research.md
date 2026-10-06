# Research notes

Everything learned while planning crickey, so it doesn't need to be redone. Unless marked otherwise, items were observed directly on 3–4 October 2026.
- **(search summary)** marks facts from a web-search summary rather than the primary page.
- **(unverified)** marks facts seen in other people's code that we haven't tested.

[plan.md](plan.md) has the goal, the golden questions and how crickey is built, and [decisions.md](decisions.md) records what was decided from these facts, by number (D1, D2, …).

## Contents
1. Access and robots.txt
2. Statsguru URL model
3. Match classes and record types
4. Filter catalogue (basic and advanced forms)
5. Minimums and sort options by view
6. Player pages
7. Player search
8. Results pages
9. Records section (first-class and List A)
10. Example data snapshot
11. Existing MCP servers and tools
12. MCP protocol and Python SDK
13. Client configuration
14. Distribution options
15. Versions and development environment
16. Open questions

## 1. Access and robots.txt

### Hosts
- Statsguru is served from `stats.cricinfo.com`; `stats.espncricinfo.com` redirects there.
- Pages are server-rendered HTML. Result tables are `<table class="engineTable">` with data rows `<tr class="data1">`.
- Generic page readers that convert pages to markdown only see the cookie banner, so pages must be parsed as raw HTML.
- `www.cricinfo.com` and `www.espncricinfo.com` return 403 to scripts (bot protection), including their robots.txt and player profile pages (for example `https://www.cricinfo.com/cricketers/babar-azam-348144` returned 403 with curl's User-Agent).

### robots.txt for stats.cricinfo.com
```
User-agent: GPTBot
Disallow: /

User-agent: Google-Extended
Disallow: /

User-agent: *
Crawl-delay: 15
Disallow: /*wrappertype=print
Disallow: /*template=results
Disallow: /error
Disallow: /fragments/
Disallow: /country-fragment/
Disallow: /country-fragment2/
```
- Results pages (`template=results`) are disallowed for every crawler, and the crawl delay is 15 s.
- Filter forms (`index.html` without `template=results`), player search (`analysis.html`) and the records index aren't disallowed.

### User-Agent tests
All on the same results page: `https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;orderby=runs;size=10;template=results;type=batting`

| User-Agent | Response |
|---|---|
| `Python-urllib/3.14` (Python's default) | 403 |
| `python-httpx/0.28.1` (httpx's default) | 403 |
| `curl/8.21.0` (curl's default) | 200 |
| `crickey/0.1` | 200 |
| httpx 0.28.1 sending `curl/8.21.0` (4 Oct) | 200 |

Earlier on 3 Oct, `Mozilla/5.0` and a full Chrome-like User-Agent also got 200 on form, results and player pages.

## 2. Statsguru URL model

| Page | URL |
|---|---|
| Filter form | `https://stats.cricinfo.com/ci/engine/stats/index.html?class=<class>;type=<type>` (add `filter=advanced` for the advanced form) |
| Results | The same parameters plus `template=results` |
| Player page | `https://stats.cricinfo.com/ci/engine/player/<id>.html?class=<class>;template=results;type=<type>[;view=<view>]` |
| Player search | `https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=<name>;template=analysis` |
| Records index | `https://stats.cricinfo.com/ci/engine/records/index.html?class=<class>` |

- **Separators:** parameters are separated by `;`, and `&` also works. Cricinfo redirects every query to a standard URL with parameters sorted alphabetically, for example `?class=2;orderby=hundreds;qualmin1=10;qualval1=hundreds;template=results;type=batting`.
- **Repeated keys:** a live T20I team results query with `result=1;result=2;size=10` returned 200 and kept the standard URL as `class=3;result=1;result=2;size=10;template=results;type=team`, so checkbox groups are encoded as repeated keys. Only that already-sorted order was checked.
- **Repeated keys for dropdown filters:** checked live on 5 Oct 2026 with `size=10` and curl's User-Agent. For each row, the filter summary applied repeated keys as “one of”.

| Parameter | Checked query | Filter summary text |
|---|---|---|
| `team` | `?class=1;continent=2;orderby=wickets;size=10;team=1;team=3;template=results;type=bowling` | `Primary team England or South Africa On continent Asia` |
| `opposition` | `?class=1;opposition=1;opposition=3;orderby=wickets;size=10;template=results;type=bowling` | `Opposition team England or South Africa` |
| `host` | `?class=1;host=6;host=7;orderby=wickets;size=10;template=results;type=bowling` | `Host country India or Pakistan` |
| `ground` | `?class=1;ground=131;ground=132;orderby=wickets;size=10;template=results;type=bowling` | `At ground AUS: Adelaide Oval or AUS: Sydney Cricket Ground` |
| `season` | `?class=1;orderby=wickets;season=2025;season=2026;size=10;template=results;type=bowling` | `Season 2025 or 2026` |
| `trophy` | `?class=1;orderby=wickets;size=10;template=results;trophy=1;trophy=2;type=bowling` | `Trophy The Ashes or Triangular Tournament` |
| `series` | `?class=1;orderby=wickets;series=1;series=2;size=10;template=results;type=bowling` | `Series England in Australia Test Series, 1876/77 or England in Australia Test Match, 1878/79` |
| `continent` | `?class=1;continent=2;continent=4;orderby=wickets;size=10;template=results;type=bowling` | `On continent Asia or Europe` |
| `final_type` | `?class=2;final_type=0;final_type=1;orderby=runs;size=10;template=results;type=batting` | `Match type tournament finals or preliminary matches` |
| `dismissal` | `?class=1;dismissal=1;dismissal=2;orderby=runs;size=10;template=results;type=batting` | `Type of dismissal caught or bowled` |
| `fow_type` | `?class=1;fow_type=1;fow_type=2;orderby=fow_score;size=10;template=results;type=fow` | `Fall of wicket dismissal out or not out` |
| `event` | `?class=1;event=1;event=2;orderby=runs;size=10;template=results;type=team` | `Innings end all out or declared Totals in terms of batting team` |

Skipped: `view`, `groupby`, `orderby`, `orderbyad`, `size` and `page` are output controls, not filters; quick-pick dropdowns such as `spanquickpick`, `agequickpick`, `runsquickpick`, `qualquickpick` and similar range shortcuts are not supported by crickey; text and numeric ranges are not dropdowns; checkbox groups were already known multi-value; radios (`toss`, `batting_fielding_first`, `captain`, `keeper`, `outs`) are not dropdowns and leaving them blank already means either state; `team_view` chooses whether team totals are from the batting or bowling side, so several values would not narrow to “one of”.
- **Links inside pages:** player profiles are `/ci/content/player/<id>.html`; matches are `/ci/engine/match/<id>.html`.
- **Date range:** `spanmin1=07+Sep+2016;spanmax1=24+Feb+2026;spanval1=span`, with dates as `DD Mon YYYY`. The form's hidden `spanmin0` and `spanmax0` hold the class's first and latest match dates. First match dates: Tests 15 Mar 1877, ODIs 05 Jan 1971, T20Is 17 Feb 2005, all T20 13 Jun 2003 and combined internationals 15 Mar 1877.
- **Minimums:** `qualval1=<field>;qualmin1=<n>`, with optional `qualmax1`. The form shows only one, but `qualval2`/`qualmin2` and `qualval3`/`qualmin3` also work: a query with three minimums returned only rows meeting all three.
- **Maximums for lower-is-better bowling fields:** checked live on 5 Oct 2026 with curl's User-Agent. Test bowling in Asia with `wickets>=100` and `qualmax2=25;qualval2=bowling_average` returned 7 rows, all with displayed bowling average at or below 25. The same check with `qualmax2=50;qualval2=bowling_strike_rate` returned 5 rows, all with displayed bowling strike rate at or below 50. Because Statsguru compares exact values while it displays truncated values, answer proof links use an inclusive display maximum: `qualmax2=27.5199;qualval2=bowling_average` with `wickets>=92` included JM Anderson's displayed 27.51 in Asia.
- **Minimum precision:** minimums are compared with exact values, to at least 4 decimals. Babar Azam's T20I average is 38.94915…; `qualmin2=38.9491` kept his row, while `qualmin2=38.9492` returned "No records available to match this query".
- **Sort:** `orderby=<field>`; `orderbyad=reverse` reverses the order.
- **Bowling sort order:** checked from saved pages on 5 Oct 2026. `orderby=wickets` returns most wickets first; `orderby=bowling_average;orderbyad=reverse` returns higher averages before lower averages, so the default `orderby=bowling_average` is best-first for bowling average. The same reverse page shape was checked for bowling strike rate. A live `orderby=economy_rate` Test bowling query in Asia with `wickets>=100` returned lower economy rates first (1.99, 2.01, 2.13...), so economy rate is also best-first by default. Higher-count bowling fields such as wickets, five-wicket hauls and ten-wicket matches use Statsguru's default high-to-low order.
- **Paging:** `size` is 10, 25, 50 (default), 100, 150 or 200, and `page=<n>` selects later pages. Each page shows the total, for example "Page 2 of 5 Showing 201 - 400 of 850", so the first page tells how many pages a query needs. A 200-row page is about 1 MB of HTML.
- **Truncated decimals:** averages and strike rates are cut off, not rounded. Babar Azam's T20I average is 4596 ÷ 118 = 38.949, shown as 38.94 (rounding would give 38.95). Bowling figures are truncated too (R8).
- **Trailing zeros** are dropped from displayed numbers, for example "47.8" and "55".
- **Fixed decimal places for proof thresholds:** saved pages show batting average, batting strike rate, bowling average and economy rate to at most 2 decimal places, and bowling strike rate to at most 1 decimal place, with trailing zeros dropped. Proof links that need an inclusive displayed maximum use those fixed precisions rather than the number of decimals present in the cell text.

## 3. Match classes and record types

| Class | Label | Where seen |
|---|---|---|
| 1 | Tests | Index tabs; player search ("Test matches") |
| 2 | ODIs | Index tabs |
| 3 | T20Is | Index tabs |
| 4 | First-class matches | Records index; the query engine refuses it (R9) |
| 5 | List A matches | Records index; the query engine refuses it (R9) |
| 6 | Twenty20 (all T20, including domestic and franchise leagues) | Index tabs; player search ("Twenty20 matches") |
| 8 | Women's Tests | Index "other" menu |
| 9 | Women's ODIs | Index tabs |
| 10 | Women's T20Is | Index tabs |
| 11 | All Test/ODI/T20I combined | Index tabs |
| 12 | Combined First-class, List A and Twenty20 | Records index |
| 13 | All cricket records (including minor cricket) | Records index |
| 16 | Minor cricket (Twenty20) | Records index |
| 20 | Youth Tests | Index "other" menu |
| 21 | Youth ODIs | Index "other" menu |
| 22 | Youth T20Is | Index "other" menu |
| 23 | Women's T20 (all) | Index "other" menu |

- Every class on the index page (1, 2, 3, 6, 8, 9, 10, 11, 20, 21, 22, 23) offers the same record types: `batting`, `bowling`, `fielding`, `allround`, `fow` (partnerships), `team`, `aggregate` and `official` (umpires and referees).
- Each class has its own lists of teams, grounds, series and trophies (R4).

## 4. Filter catalogue (basic and advanced forms)
Taken from the Tests batting forms: basic `index.html?class=1;type=batting` and advanced `index.html?class=1;filter=advanced;type=batting`. Other classes have different values (more teams, other trophies), so each class needs its own lists.

### Basic and advanced forms
Statsguru's own labels, in form order, with the parameter names behind them.

**Basic form:**

| Statsguru label | Parameters |
|---|---|
| Team | `team` |
| Opposition | `opposition` |
| Home or away | `home_or_away` |
| Host country | `host` |
| Ground | `ground` |
| Starting date (from, to, or quick pick) | `spanmin1`, `spanmax1`, `spanquickpick` |
| Season | `season` |
| Match result | `result` |
| View format | `view` |

**Advanced form:** all of the basic form, plus these (shown in form order, mixed in with the basic fields):

| Statsguru label | Parameters |
|---|---|
| Continent | `continent` |
| Series | `series` |
| Trophy | `trophy` |
| Tournament type | `tournament_type` |
| Match type | `final_type` |
| Day/night matches | `floodlit` |
| Toss result | `toss` |
| Batting or fielding first | `batting_fielding_first` |
| Captaincy | `captain` |
| Wicketkeeper | `keeper` |
| Debut or last match | `debut_or_last` |
| Type of Batter | `batting_hand` |
| Age at start of match | `agemin1`, `agemax1`, `agequickpick` |
| Match involving players | `search_player` |
| Match involving captains | `search_captain` |
| Innings in match | `innings_number` |
| Runs scored in an inns | `runsmin1`, `runsmax1`, `runsquickpick` |
| Batting position | `batting_positionmin1`, `batting_positionmax1`, `batting_positionquickpick` |
| Dismissed | `outs` |
| Type of dismissal | `dismissal` |
| Group figures by | `groupby` |
| Result qualifications | `qualval1`, `qualmin1`, `qualmax1`, `qualquickpick` |
| Sort results by | `orderby`, `orderbyad` |
| Results per page | `size` |

These are the batting forms. The other stat types are below.

### Other stat types
The basic bowling form has the same fields as the basic batting form. In the advanced forms, each type keeps the shared fields and swaps the batting-only ones (Type of Batter, Runs scored in an inns, Batting position, Dismissed, Type of dismissal) for its own. Form defaults are in brackets.

| Type | Type-specific fields (parameters) |
|---|---|
| Bowling | Type of Bowler (by hand) (`bowling_hand`: 1 right-arm, 2 left-arm, 3 unknown arm); Type of Bowler (by style) (`bowling_pacespin`: 1 pace, 2 spin, 3 mixture/unknown); Balls bowled in an inns (`ballsmin1`, `ballsmax1`, `ballsquickpick`; 0–588); Runs conceded (`concededmin1`, `concededmax1`, `concededquickpick`; 0–298); Wickets taken (`wicketsmin1`, `wicketsmax1`, `wicketsquickpick`; 0–10); Bowling position (`bowling_positionmin1`, `bowling_positionmax1`, `bowling_positionquickpick`; 0–11) |
| Fielding | Catches in an innings (`caughtmin1`, `caughtmax1`, `caughtquickpick`; 0–7); Stumpings in an innings (`stumpedmin1`, `stumpedmax1`, `stumpedquickpick`; 0–5) |
| All-round | All the batting, bowling and fielding fields above |
| Partnerships (`fow`) | Partnership runs (`partnership_runsmin1`, `partnership_runsmax1`, `partnership_runsquickpick`; 0–624); For wicket (`partnership_wicketmin1`, `partnership_wicketmax1`, `partnership_wicketquickpick`; 1–10); `fow_type` (1 out, 2 not out, 3 end of innings). No age, debut/last match, Captaincy or Wicketkeeper. |
| Team | Team runs (`runsmin1`, `runsmax1`, `runsquickpick`; 0–952); Team wickets (`wicketsmin1`, `wicketsmax1`, `wicketsquickpick`; 0–10); Team balls received/bowled (`ballsmin1`, `ballsmax1`, `ballsquickpick`; 0–2012); `event` (1 all out, 2 declared, 3 target reached, 4 forfeited); Team totals for (`team_view`: blank for the batting team, `bowl` for the bowling team). No age, debut/last match, Captaincy or Wicketkeeper. |
| Aggregate | None. It also lacks opposition, home/away, result, toss, Captaincy, debut/last match, age, Batting or fielding first, Wicketkeeper, Innings in match and Group figures by; it still has match-involving player and captain searches. |

**Bowling quick picks:**
- `ballsquickpick`: 1 six or less, 2 30 or less, 3 30 or more, 4 60 or less, 5 60 or more, 6 100 or more, 7 200 or more.
- `concededquickpick`: 1 0 to 9, 2 less than 20, 3 less than 40, 4 50 and above, 5 80 and above, 6 100 and above, 7 200 and above.
- `wicketsquickpick`: 1 none, 2 4 or more, 3 5 or more, 4 7 or more, 5 10.
- `bowling_positionquickpick`: 1 opening (1–2), 2 first change (3), 3 second change (4), 4 others (5–11).

**Views (`view`)** beyond the batting list: all-round adds `results` and `awards`; team adds `results`, `extras` and `extras_innings`; aggregate has overall, `match`, `results`, `series`, `ground`, `host`, `year`, `season` and `extras`.

### Values
**Teams (`team`, `opposition`):** 40 Afghanistan, 2 Australia, 25 Bangladesh, 1 England, 140 ICC World XI, 6 India, 29 Ireland, 5 New Zealand, 7 Pakistan, 3 South Africa, 8 Sri Lanka, 4 West Indies, 9 Zimbabwe. Player pages also list 32 Nepal, 15 Netherlands and 27 United Arab Emirates as oppositions.

**Host countries (`host`):** 2 Australia, 25 Bangladesh, 1 England, 6 India, 29 Ireland, 5 New Zealand, 7 Pakistan, 3 South Africa, 8 Sri Lanka, 27 United Arab Emirates, 4 West Indies, 9 Zimbabwe.

**Continents (`continent`):** 1 Africa, 3 Americas, 2 Asia, 4 Europe, 5 Oceania.

**Long lists:**
- `ground`: 127 values, from `131` (AUS: Adelaide Oval) to `261` (ZIM: Queens Sports Club, Bulawayo).
- `season`: 227 values, from `1876/77` to `2026`.
- `series`: 878 values, from `1` (England in Australia Test Series, 1876/77) to `17461` (Pakistan in England Test Series, 2026).

**Trophies (`trophy`):** each class has its own list: 24 for Tests, 90 for ODIs, 127 for T20Is, 171 for all T20 and 237 for combined internationals. Examples: 1 The Ashes (Tests), 12 World Cup and 44 ICC Champions Trophy (ODIs), 89 ICC Men's T20 World Cup (T20Is), 117 Indian Premier League and 205 Pakistan Super League (all T20). These IDs match the ones in Cricinfo's league page URLs (R9).

**Other classes:**
- ODIs list 29 teams, T20Is 110 (including associates such as 187 Qatar and 36 Japan), combined internationals 113, and all T20 562.
- In all T20, national teams keep their IDs (7 Pakistan, 6 India), and domestic and franchise teams have their own, for example 5799 Lahore Qalandars, 5793 Karachi Kings, 4346 Mumbai Indians and 4849 Sydney Sixers.
- In the advanced batting forms for D3's built-in classes, the `team` and `opposition` lists are identical, so the built-in team table can be used for both filters.
- The class 2, 3 and 6 batting forms differ from Tests: `result` offers 5 (no result) instead of 4 (drawn), `final_type` includes 3 tournament semi-finals and 4 tournament quarter-finals, `innings_number` is 1–2 only, `batting_hand` includes 3 unknown, and there is no batting `view=match`.
- The class 3, 6 and 11 batting forms additionally have `floodlit=3` for night matches and `dismissal=9` for hit the ball twice. The T20I batting form has age range defaults of 14–62 instead of 14–52.

**Choice filters.** The forms' checkboxes allow several choices. The forms' selects and radios allow one choice, but the engine accepts repeated keys for the dropdowns listed in R2. Leaving a radio blank means "either".

| Parameter | Values |
|---|---|
| `home_or_away` | 1 home, 2 away, 3 neutral |
| `result` | 1 won, 2 lost, 3 tied, 4 drawn (player pages use 5 for no result) |
| `tournament_type` | 2 two-team series, 3 three- or four-team tournaments, 5 five or more teams |
| `final_type` | 1 finals, 0 preliminary matches |
| `floodlit` | 1 day, 2 day/night |
| `toss` | 1 won, 2 lost |
| `batting_fielding_first` | 1 batting first, 2 fielding first |
| `captain` | 1 as captain, 0 not as captain |
| `keeper` | 1 as designated wicketkeeper, 0 not as wicketkeeper |
| `debut_or_last` | 1 career debut, 2 last career match, 3 team debut, 4 last match for team |
| `batting_hand` | 1 right-handed, 2 left-handed |
| `innings_number` | 1, 2, 3, 4 |
| `outs` | 1 out, 0 not out, absent or did not bat |
| `dismissal` | 1 caught, 2 bowled, 3 lbw, 4 run out, 5 stumped, 6 hit wicket, 7 handled the ball, 8 obstructing the field, 11 retired out, 12 not out, 13 retired not out (hurt) |
| `view` | blank for overall, `innings`, `match`, `series`, `ground`, `host`, `opposition`, `year`, `season` |
| `groupby` | blank for individual players, `innings` (each team innings), `match`, `series`, `tour`, `team`, `opposition`, `ground`, `host`, `continent`, `year`, `season`, `decade`, `overall` (overall aggregate) |
| `orderbyad` | blank for the default order, `reverse` |

**Ranges.** Hidden `*min0` and `*max0` fields hold the defaults.

| Filter | Parameters | Form defaults |
|---|---|---|
| Dates | `spanmin1`, `spanmax1`, `spanval1=span` | The class's first and latest match dates |
| Age at match start | `agemin1`, `agemax1`, `ageval1=age` | 14 to 52 |
| Runs in an innings | `runsmin1`, `runsmax1`, `runsval1=runs` | 0 to 400 |
| Batting position | `batting_positionmin1`, `batting_positionmax1`, `batting_positionval1=batting_position` | 0 to 12 |
| Minimums | `qualval1`, `qualmin1`, `qualmax1`, plus hidden 2 and 3 | None |

**Quick picks.** The form's JavaScript turns these into the ranges above.
- `spanquickpick`: 1 matches starting this year, 2 matches starting last year, 3 last 12 months, 4–8 last 2, 3, 4, 5 and 10 years, 9–24 decades from the 2020s back to the 1870s, 25–27 the 21st, 20th and 19th centuries.
- `agequickpick`: 1 teenager, 2 22 or less, 3 25 or less, 4 25 and above, 5 30 and above, 6 40 and above.
- `runsquickpick`: 1 no runs, 2 single figures, 3 0 to 25, 4 25 and above, 5 0 to 49, 6 50 and above, 7 nervous nineties, 8 centuries, 9 double centuries.
- `batting_positionquickpick`: 1 openers (1–2), 2 upper order (1–3), 3 middle order (4–7), 4 top order (1–7), 5 tail (8–11), 6–14 numbers 3 to 11.
- `qualquickpick`: 1 0 only, 2 1 and above, 3 5 and above, 4 0 to 9, 5 10 and above, 6 20 and above, 7 0 to 30, 8 0 to 49, 9 50 to 99, 10 75 and above, 11 90 to 99, 12 100 plus, 13 200 plus, 14 500 plus, 15 1000 plus, 16 2000 plus, 17 5000 plus, 18 10000 plus.

**Match involving players or captains:** `search_player` and `search_captain` are name searches.
- Submitting the form with `search_player=<name>` returns it with one checkbox `player_involve=<id>` per matching player, plus `player_involve_type=none` for "not including this player". Captains use `captain_involve` and `captain_involve_type`.
- These IDs differ from player-page IDs: Babar Azam is 56880 here and 348144 on player pages.
- Checked on T20I team results: `player_involve=56880` gave Pakistan 145 matches (all of his T20Is), and `captain_involve=56880` gave 85 (the ones he captained).

## 5. Minimums and sort options by view
Each advanced form contains one `qualval1` list and one `orderby` list per view (ids `havingselect_<type>_<view>` and `orderbyselect_<type>_<view>`), and shows the pair for the chosen view. It also includes lists for views that exist only on player pages (cumulative, partnerships, dismissals).

### Batting

| View | `qualval1` (minimum) fields | Extra `orderby` (sort) fields |
|---|---|---|
| Overall, ground, host, opposition, series | matches, innings, notouts, outs, runs, minutes, balls_faced, batting_average, batting_strike_rate, hundreds, fifty_plus, ducks, fours, sixes | player, start, high_score |
| year, season | The same, plus `year` or `season` | player, high_score (no `start`) |
| innings | batted_score, minutes, balls_faced, fours, sixes, batting_strike_rate | player, start, age, batting_position, dismissal, innings_number |
| match | runs, minutes, balls_faced, fours, sixes, batting_strike_rate, innings, notouts, outs, high_score, batting_average, hundreds, fifty_plus, ducks | player, start, age, batting_score1, batting_score2 |
| cumulative, reverse_cumulative | batting_average, batting_strike_rate | start |
| fow_summary | start, fow_innings, fow_notouts, fow_outs, fow_runs, fow_average, fow_hundreds, fow_fifty_plus | partner, fow_high_score |
| fow_list | start, fow_wicket, fow_score, fow_in, fow_out, fow_innings_number | partner |
| dismissal_summary | dis_dismissals, dis_bowled, dis_caught_fielder, dis_caught_keeper, dis_stumped, dis_lbw, dis_hit_wicket, dis_run_out, dis_other, dis_not_out, dis_average, dis_ducks | default, start |
| dismissal_list | dis_runs, dis_innings_number | start, dis_how_out, dis_fielder, dis_bowler |
| bowler_summary | dis_matches, dis_dismissals, dis_bowled, dis_caught_fielder, dis_caught_keeper, dis_stumped, dis_lbw, dis_hit_wicket, dis_matches_per_dismissal, dis_average, dis_ducks | dis_bowler, dis_span |
| fielder_summary | dis_matches, dis_dismissals, dis_caught_fielder, dis_caught_keeper, dis_stumped, dis_matches_per_dismissal, dis_average, dis_ducks | dis_fielder, dis_span |

The sort list for each view contains all its minimum fields plus the extras shown.

**Field meanings:**
- **Batting:** matches = matches played; innings = innings batted; notouts = not outs; outs = batting dismissals; runs = runs scored; minutes = minutes batted; balls_faced = balls faced; batting_average; batting_strike_rate; hundreds = hundreds scored; fifty_plus = fifties only (scores of 50-99); ducks = ducks scored; fours, sixes = boundaries; high_score = highest innings score; batted_score = runs in the innings (innings view); batting_score1, batting_score2 = runs in the 1st and 2nd innings of a match.
- **Context:** player = player name; start = start date; age = age at the start of the match; batting_position = batting order position; dismissal = method of dismissal; innings_number = innings number in the match; year = year of match start; season = match season.
- **Partnerships (`fow_*`):** fow_wicket = fall-of-wicket number; fow_score = partnership runs; fow_in, fow_out = team score at the partnership's start and end; fow_innings = number of partnerships; fow_notouts = unbroken partnerships; fow_outs = broken partnerships; fow_runs = total partnership runs; fow_high_score = highest partnership; fow_average = average partnership per dismissal; fow_hundreds = century partnerships; fow_fifty_plus = partnerships of fifty or more; partner = partner name.
- **Dismissals (`dis_*`):** dis_matches = matches against each other; dis_dismissals = total dismissals; dis_bowled, dis_caught_fielder, dis_caught_keeper, dis_stumped, dis_lbw, dis_hit_wicket, dis_run_out, dis_other, dis_not_out = counts by type; dis_average = average score upon dismissal; dis_ducks = ducks; dis_matches_per_dismissal = matches per dismissal; dis_runs = batter's runs in the innings; dis_innings_number = innings number in the match; dis_how_out = method of dismissal; dis_bowler, dis_fielder = bowler or fielder who took the dismissal; dis_span = playing span against each other.

### Bowling

| View | `qualval1` (minimum) fields | Extra `orderby` (sort) fields |
|---|---|---|
| Overall, ground, host, opposition, series | matches, innings_bowled, balls, overs, maidens, conceded, wickets, bowling_average, economy_rate, bowling_strike_rate, four_plus_wickets, five_wickets, ten_wickets | player, start, bbi, bbm |
| year, season | The same, plus `year` or `season` | player, bbi, bbm (no `start`) |
| innings | overs, maidens, conceded, wickets, bowling_average, economy_rate, bowling_strike_rate, bowling_position | player, start, age |
| match | innings_bowled, overs, maidens, conceded, wickets, bowling_average, economy_rate, bowling_strike_rate, four_plus_wickets, five_wickets, ten_wickets | player, start, age, bbi |
| cumulative, reverse_cumulative | bowling_average, economy_rate, bowling_strike_rate | start |
| dismissal_summary | dis_dismissals, dis_bowled, dis_caught_fielder, dis_caught_keeper, dis_stumped, dis_lbw, dis_hit_wicket, dis_average, dis_ducks | default, dis_span |
| dismissal_list | dis_runs, dis_innings_number | start, dis_batsman, dis_how_out, dis_fielder |
| batsman_summary | dis_matches, dis_dismissals, dis_bowled, dis_caught_fielder, dis_caught_keeper, dis_stumped, dis_lbw, dis_hit_wicket, dis_matches_per_dismissal, dis_average, dis_ducks | dis_batsman, dis_span |
| fielder_summary | dis_matches, dis_dismissals, dis_caught_fielder, dis_caught_keeper, dis_stumped, dis_matches_per_dismissal, dis_average, dis_ducks | dis_fielder, dis_span |

**Field meanings:** matches = matches played; innings_bowled = innings bowled in; balls = balls bowled; overs = overs bowled; maidens = maidens earned; conceded = runs conceded; wickets = wickets taken; bowling_average; economy_rate; bowling_strike_rate; four_plus_wickets = four wickets in an innings; five_wickets = five wickets in an innings; ten_wickets = ten wickets in a match; bbi, bbm = best bowling in an innings and in a match; bowling_position = bowling order position; dis_batsman = batter dismissed. The `dis_*` fields mean the same as for batting.

### Other stat types (overall view)

| Type | `qualval1` (minimum) fields | Extra `orderby` (sort) fields |
|---|---|---|
| Fielding | matches, matches_keeper, matches_fielder, innings_fielded, dismissals, caught, stumped, caught_keeper, caught_fielder, dismissals_per_inns | player, start, age, max_dismissals |
| All-round | The batting and bowling overall fields, the fielding fields, and allround_average (batting average minus bowling average) | player, start, high_score, bbi, bbm, max_dismissals |
| Partnerships | fow_innings, fow_notouts, fow_outs, fow_runs, fow_average, fow_balls_faced, fow_run_rate, fow_hundreds, fow_fifty_plus | partners, start, fow_high_score |
| Team | matches, won, lost, tied, drawn, no_result, win_loss_ratio, percentage_won, percentage_lost, percentage_drawn, percentage_tied, percentage_no_result, runs, wickets, balls, team_average, runs_per_over, team_innings, team_high_score, team_low_score | team, start |
| Aggregate | The team fields except lost, win_loss_ratio, team_innings, team_high_score and team_low_score | start |

Each type also has per-view lists, read the same way from its form. Fielding, all-round, partnerships, team and aggregate are not "overall for every view": for example team `innings` has `team_score`, `team_wickets`, `team_overs`, `target` and `lead` but not `won`; all-round `awards` has `awards_match` and `awards_series`; partnerships `innings` has `fow_score`; year and season views add `year` or `season` respectively.

## 6. Player pages
- **URL:** `https://stats.cricinfo.com/ci/engine/player/<id>.html?class=<class>;template=results;type=<type>[;view=<view>]`
- **Types (`type`):** `allround`, `batting`, `bowling`, `fielding`.
- **Views (`view`):** the form offers 24 options. The grouping below is inferred from the form's layout.
  - Batting: blank for career summary, `innings`, `match`, `cumulative`, `reverse_cumulative`, `series`, `ground`, `results` (match results), `awards_match`, `awards_series`, `fow_summary` (partnership summary), `fow_list`, `dismissal_summary`, `bowler_summary`, `fielder_summary`, `dismissal_list`.
  - Bowling: `dismissal_summary` (wickets summary), `batsman_summary` (batters dismissed), `fielder_summary`, `dismissal_list` (list of wickets).
  - Fielding: `dismissal_summary`, `batsman_summary`, `bowler_summary`, `dismissal_list`.
- **Filters:** `opposition`, `host` and `ground` (lists specific to the player; Babar Azam's ODIs have 14 oppositions, 12 hosts and 51 grounds), `home_or_away`, dates (`spanmin1` and `spanmax1`), `spanquickpick`, `season` and `result` (1 won, 2 lost, 3 tied, 5 no result).
- **Career span:** the player's form page for a format (the same URL without `template=results`, which robots.txt allows) has hidden `spanmin0` and `spanmax0` set to the player's first and last match dates in that format. Babar Azam's T20Is: 07 Sep 2016 to 24 Feb 2026; his ODIs: 31 May 2015 to 04 Jun 2026.
- **Format tabs** show each format's span. For Babar Azam: "Tests (2016/17 - 2026)", "ODIs (2015 - 2026)" and "T20Is (2016 - 2025/26)", plus an "other" menu with class 11 (All Test/ODI/T20I), 6 (Twenty20), 21 (Youth ODIs) and 22 (Youth T20Is).
- **Career summary columns:**
  - T20I batting: Span, Mat, Inns, NO, Runs, HS, Ave, BF, SR, 100, 50, 0, 4s, 6s.
  - T20I bowling: Span, Mat, Inns, Overs, Mdns, Runs, Wkts, BBI, Ave, Econ, SR, 4, 5.
  - The summary page also has tables split by a "Grouping" column (for example one row per opposition: "v Afghanistan", "v Australia").
- **Innings list (`view=innings`):** one row per match, after the same "Career averages" table, so one request gives both.
  - Batting columns: Runs, Mins, BF, 4s, 6s, SR, Pos, Dismissal, Inns, (blank), Opposition, Ground, Start Date, then the match label (for example "T20I # 566").
  - Bowling columns: Overs, Mdns, Runs, Wkts, Econ, Pos, Inns, (blank), Opposition, Ground, Start Date, then the match label.
- **Filters in the URL:** player pages accept advanced-form filters that their own form doesn't show, including dates and `trophy`. Babar Azam's T20I summary page links innings lists such as `…/player/348144.html?batting_fielding_first=1;class=3;result=1;template=results;type=batting;view=innings`.
- **Filtered pages:** with any filter, "Career averages" shows an "unfiltered" row and a "filtered" row (examples in R10).
- **Player-page repeated filters:** checked live on 5 Oct 2026 with curl's User-Agent. JM Anderson's Test bowling page with `host=6;host=7` showed a filtered row, so player pages also apply repeated host keys as "one of".
- **Bowling player pages with continent filters:** checked live on 5 Oct 2026. JM Anderson (ID 8608) with `class=1;continent=2;type=bowling` showed the filtered Asia row as 32 Tests, 92 wickets, average 27.51, economy 2.59 and strike rate 63.6. DW Steyn (ID 47492, found from saved Statsguru links and player search) with the same filter showed 22 Tests, 92 wickets, average 24.11, economy 3.36 and strike rate 42.9.
- **Profile link:** the summary row's "Profile" link is `/ci/content/player/<id>.html`. On stats.cricinfo.com it redirects (302) to `https://www.espncricinfo.com/ci/content/player/<id>.html`, part of the player's profile on www, which scripts can't fetch (R1).
- **Same IDs:** Statsguru and the www profile pages use the same player ID. For example, 348144 is Babar Azam on Statsguru and in `https://www.cricinfo.com/cricketers/babar-azam-348144`.

## 7. Player search
- `https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=babar;template=analysis` returned 15 people.
- Each row has the player's name (with the full name in brackets when the name uses initials) and country code, then one entry per format with its span and match count. Each entry links to `/ci/engine/player/<id>.html?class=<class>;type=allround`.
- Example row: "Babar Azam PAK", with "Test matches player (2016/17 - 2026, 66 matches)", "One-Day Internationals player (2015 - 2026, 143 matches)", "Twenty20 Internationals player (2016 - 2025/26, 145 matches)", "Combined Test, ODI and T20I player (2015 - 2026)", "Under-19s Youth One-Day Internationals player (2009/10 - 2012, 36 matches)", "Under-19s Youth Twenty20 Internationals player (2009/10, 1 match)" and "Twenty20 matches player (2012/13 - 2026, 359 matches)".
- Results mix international, domestic-only and youth players who share a name, for example Babar Hayat (Hong Kong), Muhammad Babar (Spain) and Zulfiqar Babar (Pakistan). Country codes can be combined ("BHM/PAK"), and other searches also return women and officials. Name lookups must filter by class, and ideally by country.

## 8. Results pages
- **Batting columns (overall view):**
  - ODIs: Player, Span, Mat, Inns, NO, Runs, HS, Ave, BF, SR, 100, 50, 0
  - T20Is: the ODI columns plus 4s and 6s
  - All T20: Player, Span, Mat, Inns, NO, Runs, HS, Ave, 100, 50, 0 (no balls faced or strike rate)
  - All Test/ODI/T20I combined: Player, Span, Mat, Inns, NO, Runs, HS, Ave, 100, 50, 0 (no balls faced or strike rate)
  - Tests: Player, Span, Mat, Inns, NO, Runs, HS, Ave, 100, 50, 0 (no balls faced or strike rate)
- All T20 player rows have class `data2`; player cells contain only the linked name with no `(COUNTRY)` suffix, with teams in a following `note` row as bracketed comma-separated names.
- The `50` batting column counts fifties only (scores of 50-99), not hundreds plus fifties. For example, Tendulkar's ODI row shows 49 hundreds and 96 fifties.
- **Bowling columns (overall view):**
  - Tests: Player, Span, Mat, Inns, Balls, Runs, Wkts, BBI, BBM, Ave, Econ, SR, 5, 10
  - ODIs: Player, Span, Mat, Inns, Balls, Runs, Wkts, BBI, Ave, Econ, SR, 4, 5
  - T20Is: Player, Span, Mat, Inns, Overs, Mdns, Runs, Wkts, BBI, Ave, Econ, SR, 4, 5 (overs such as "449.5", not balls)
- **Fielding columns (Tests overall view):** Player, Span, Mat, Inns, Dis, Ct, St, Ct Wk, Ct Fi, MD, D/I. `MD` is max dismissals in an innings, for example "6 (6ct 0st)".
- **All-round columns (Tests overall view):** Player, Span, Mat, Runs, HS, Bat Av, 100, Wkts, BBI, Bowl Av, 5, Ct, St, Ave Diff.
- **Partnership columns (Tests overall view):** Partners, Span, Inns, NO, Runs, High, Ave, 100, 50. Partnership rows use a `Partners` cell, not `Player`, and name both players plus the team, for example "R Dravid, SR Tendulkar (IND)".
- **Team columns (T20I overall view with player-involve filter):** Team, Span, Mat, Won, Lost, Tied, NR, W/L, Ave, RPO, Inns, HS, LS. Team cells link to `/ci/content/team/<id>.html` and use team names, not player links.
- **Aggregate columns (Tests overall view):** Span, Mat, Won, Tied, Draw, Runs, Wkts, Balls, Ave, RPO. The overall aggregate row has no player or team cell.
- **Bowling decimals:** averages and economy rates (2 decimals) and strike rates (1 decimal) are truncated like batting figures. In all 334 cases on three pages where truncating and rounding differ, the page showed the truncated value.
- **Player cell:** "Name (COUNTRY)", for example "Babar Azam (PAK)". Players who represented several teams list them all, for example "Rashid Khan (AFG/ICC)". T20I lists include players from associate nations (for example QAT and JPN).
- **Player-search country codes:** saved player-search, results and player pages use country/team codes from Statsguru rows and ground prefixes. Codes that differ from obvious first letters include Austria = AUT, Bermuda = BER, Indonesia = INA, Malaysia = MAS, Saudi Arabia = KSA, Sierra Leone = SLE, Scotland = SCOT, Netherlands = NED and Nepal = NEP. Italy appeared only as the ground prefix ITA in saved pages, not as a player row.
- **Sort caption:** for example "Ordered by runs scored (descending)" or "Ordered by wickets taken (descending)".
- **No results:** the table has one row reading "No records available to match this query".
- **Freshness note:** each results page says "Statsguru includes the following current or recent <format> matches:", followed by match names, dates and links (`/ci/engine/match/<id>.html`, labelled like "Test # 2635").

## 9. Records section (first-class and List A)
- Statsguru's query engine returned 503 for `class=4` and `class=5` (two requests 16 s apart), while `class=6` returned 200 at the same time. On 4 Oct a `class=4` results query returned 400 with an error page ("This page does not exist or has been moved"), so the query engine doesn't serve first-class or List A figures.
- `https://stats.cricinfo.com/ci/engine/records/index.html` returned 200 and links classes 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 16, 20, 21 and 23 (labels in R3).
- `records/index.html?class=4` (title "Records | First-class matches | Cricinfo.com") returned 200. It links fixed record lists at `/ci/content/records/<id>.html` and team records at `/ci/engine/records/index.html?category=2;class=4`.
- `/ci/content/records/251072.html` returned 503 on 3 Oct but 200 on 4 Oct, so record lists are reachable. It's a table headed Player, Span, Mat, Inns, NO, Runs, HS, Ave, 100, 50, led by JB Hobbs (1905–1934) with 199 hundreds.
- The List A index (`records/index.html?class=5`) links 40 record lists, for example 282830 Most runs in career and 117937 Highest innings totals.

First-class record lists (the page had 44 such links; these were captured):

| ID | Record |
|---|---|
| 135790 | Highest innings totals |
| 283975 | Highest fourth innings totals |
| 283989 | Lowest innings totals |
| 283998 | Shortest completed innings (by balls) |
| 284268 | Most runs in career |
| 94177 | Most runs in an innings |
| 146057 | Most runs scored off one over |
| 284199 | Highest career batting average |
| 282925 | Double hundred on debut |
| 251072 | Most hundreds in a career |
| 282971 | Hundreds in consecutive innings |
| 283002 | Fastest innings |
| 1510652 | Fastest fifties (by balls) |
| 283145 | Longest individual innings (by minutes) |
| 283986 | Unusual dismissals |
| 283196 | Most wickets in career |
| 209288 | Best figures in an innings |
| 283212 | Best figures in a match |
| 283259 | Best career bowling average |
| 283308 | Worst career bowling average |
| 283847 | Worst career bowling average (without qualification) |

The Statsguru header links league record pages on www.cricinfo.com (blocked to scripts) with numeric IDs in the path, for example IPL 117, PSL 205 and World Cup 12. For the men's competitions checked against the class 2, 3 and 6 forms, these are the same as Statsguru's `trophy` values (R4).

## 10. Example data snapshot
Useful for tests and as known answers.

**ODI batters with at least 10 hundreds:** 64 players on one page. Babar Azam is 17th:
`https://stats.cricinfo.com/ci/engine/stats/index.html?class=2;orderby=hundreds;qualmin1=10;qualval1=hundreds;size=200;template=results;type=batting`

| Player | Span | Mat | Inns | NO | Runs | HS | Ave | BF | SR | 100 | 50 | 0 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Babar Azam (PAK) | 2015-2026 | 143 | 140 | 16 | 6626 | 158 | 53.43 | 7652 | 86.59 | 20 | 38 | 5 |

For golden question 1, his innings per hundred are 140 ÷ 20 = 7.

**Batters with at least 1 hundred** (the loosest form of golden questions 1 and 2), fetched 3 Oct 2026:
- ODIs: 520 players, 3 pages of 200. `https://stats.cricinfo.com/ci/engine/stats/index.html?class=2;orderby=hundreds;qualmin1=1;qualval1=hundreds;size=200;template=results;type=batting`
- Tests: 850 players, 5 pages of 200. The same query with `class=1`, plus `page=2` and so on for later pages. Rank 200 has 7 hundreds and rank 400 has 3, so a minimum of 4 hundreds fits in 2 pages.

**Default-minimum count checks** (size=10 pages, fetched 4 Oct 2026 for issue #7):
- Hundreds minimums: Tests `hundreds>=5` 292 rows; ODIs `hundreds>=5` 144; T20Is `hundreds>=1` 191; all T20 `hundreds>=3` 121; combined internationals `hundreds>=10` 207.
- Candidate innings-per-fifty-plus innings minimums were too broad: Tests `innings>=20` 1225 rows; ODIs `innings>=20` 1060; T20Is `innings>=20` 1064; all T20 `innings>=30` 2347; combined internationals `innings>=30` 1965.
- Candidate balls-per-dismissal outs minimums were too broad: ODIs `outs>=20` 907 rows; T20Is `outs>=15` 1114; all T20 `outs>=25` 2167; combined internationals `outs>=40` 1279.

**Bowling default-minimum count checks** (size=10 pages, fetched 5 Oct 2026 for issue #48):
- Wickets floors: Tests `wickets>=100` 206 rows; ODIs `wickets>=100` 178; T20Is `wickets>=50` 236 (from the T20I bowling check below); all T20 `wickets>=100` 446; combined internationals `wickets>=200` 193.
- Filtered-floor counts, measured with only a recent 10-year span from 5 Oct 2016 to 5 Oct 2026: Tests `wickets>=30` 110 rows; ODIs `wickets>=30` 190; T20Is `wickets>=20` 681; all T20 `wickets>=30` 1188, so the filtered all T20 floor is `wickets>=50`, which gives 706 rows; combined internationals `wickets>=50` 382. Longer spans, such as a 20-year career, can return more rows than D10 allows; the tool then stops with its "too broad" message.

**Filtered count and comparison floors** (fetched 6 Oct 2026 for issue #53):
- Test bowling in Asia for England or South Africa (`team=1;team=3;continent=2`) with `wickets>=1`: 178 rows. JM Anderson and DW Steyn are tied first with 92 wickets, then MJ Leach 79, and GP Swann and DL Underwood 73 each.
- T20I batting in the UAE (`host=27`), all time: `runs>=1000` gives 4 rows. V Kohli (ID 253802) has 344 runs there in 8 innings with 3 not outs, average 68.8 and strike rate 134.9; `runs>=344` gives 43 rows.
- In the same table, Shoaib Malik (ID 42657) has 549 runs, average 27.45 and strike rate 122; `runs>=549` gives 18 rows, and adding `batting_average>=27.45` and `batting_strike_rate>=122` gives 7: Malik and the 6 batters who beat him on both.

**Babar Azam (ID 348144), T20I batting career:** 2016–2026, 145 matches, 136 innings, 18 not outs, 4596 runs, highest 122, average 38.94, 3590 balls, strike rate 128.02, 3 hundreds, 39 fifties, 10 ducks, 477 fours and 80 sixes. His innings list has 145 rows, from 07 Sep 2016 (v England, Manchester) to 24 Feb 2026 (v England, Pallekele).
- Career: `https://stats.cricinfo.com/ci/engine/player/348144.html?class=3;template=results;type=batting`
- Innings: the same URL plus `;view=innings`
- 2020–2021 only (`spanmin1=01+Jan+2020;spanmax1=31+Dec+2021;spanval1=span`): 37 matches, 32 innings, 2 not outs, 1215 runs, average 40.5, strike rate 131.06.

**T20I batters with at least 1,000 runs between 07 Sep 2016 and 24 Feb 2026 (Babar Azam's span):** 182 players on one page. Babar Azam is 11th by average.
`https://stats.cricinfo.com/ci/engine/stats/index.html?class=3;orderby=batting_average;qualmin1=1000;qualval1=runs;size=200;spanmax1=24+Feb+2026;spanmin1=07+Sep+2016;spanval1=span;template=results;type=batting`

**The same query, also requiring average ≥ 38.94 and strike rate ≥ 128.02** (golden question 3), returned 7 rows: Babar Azam and the 6 batters who beat him on both.
`https://stats.cricinfo.com/ci/engine/stats/index.html?class=3;orderby=batting_average;qualmin1=1000;qualmin2=38.94;qualmin3=128.02;qualval1=runs;qualval2=batting_average;qualval3=batting_strike_rate;size=200;spanmax1=24+Feb+2026;spanmin1=07+Sep+2016;spanval1=span;template=results;type=batting`

| Player | Span | Runs | Ave | SR |
|---|---|---|---|---|
| Karanbir Singh (AUT) | 2024-2025 | 1721 | 47.8 | 169.22 |
| NT Tilak Varma (IND) | 2023-2026 | 1290 | 44.48 | 141.6 |
| V Kohli (IND) | 2017-2024 | 2531 | 44.4 | 138.07 |
| DA Warner (AUS) | 2016-2024 | 1616 | 41.43 | 144.8 |
| K Kadowaki-Fleming (JPN) | 2022-2025 | 1669 | 40.7 | 145.13 |
| Muhammad Tanveer (QAT) | 2019-2025 | 1980 | 39.6 | 133.42 |
| Babar Azam (PAK) | 2016-2026 | 4596 | 38.94 | 128.02 |

**Babar Azam in ODI World Cups** (golden question 5): his ODI page with `trophy=12` shows 17 matches (2019–2023), 17 innings, 2 not outs, 794 runs, highest 101*, average 52.93, strike rate 85.74, 1 hundred and 7 fifties.
`https://stats.cricinfo.com/ci/engine/player/348144.html?class=2;template=results;trophy=12;type=batting`

**T20I bowlers with at least 50 wickets:** 236 players, 2 pages of 200.
`https://stats.cricinfo.com/ci/engine/stats/index.html?class=3;orderby=wickets;qualmin1=50;qualval1=wickets;size=200;template=results;type=bowling`

| Player | Span | Mat | Overs | Runs | Wkts | BBI | Ave | Econ | SR |
|---|---|---|---|---|---|---|---|---|---|
| Rashid Khan (AFG/ICC) | 2015-2026 | 118 | 449.5 | 2763 | 197 | 5/3 | 14.02 | 6.14 | 13.7 |
| AU Rashid (ENG) | 2009-2026 | 153 | 529.4 | 4006 | 171 | 4/2 | 23.42 | 7.56 | 18.5 |
| IS Sodhi (NZ) | 2014-2026 | 142 | 465.2 | 3798 | 165 | 4/12 | 23.01 | 8.16 | 16.9 |

**Shaheen Shah Afridi (ID 1072470), T20I bowling career:** 2018–2026, 103 matches, 103 innings, 370.3 overs, 3 maidens, 2904 runs, 136 wickets, best 4/22, average 21.35, economy 7.83, strike rate 16.3, three four-wicket innings and no five-wicket innings. His innings list has 103 rows, starting 3 Apr 2018 (v West Indies, Karachi).
`https://stats.cricinfo.com/ci/engine/player/1072470.html?class=3;template=results;type=bowling;view=innings`

**Most wickets:** in Tests, M Muralidaran 800, SK Warne 708 and JM Anderson 704; in ODIs, M Muralidaran 534, Wasim Akram 502 and Waqar Younis 416.

**Most Test runs (top 3):**

| Player | Span | Mat | Inns | NO | Runs | HS | Ave | 100 | 50 | 0 |
|---|---|---|---|---|---|---|---|---|---|---|
| SR Tendulkar (IND) | 1989-2013 | 200 | 329 | 33 | 15921 | 248* | 53.78 | 51 | 68 | 14 |
| JE Root (ENG) | 2012-2026 | 169 | 309 | 26 | 14278 | 262 | 50.45 | 41 | 69 | 15 |
| RT Ponting (AUS) | 1995-2012 | 168 | 287 | 29 | 13378 | 257 | 51.85 | 41 | 62 | 17 |

**Current or recent Tests listed on 3 Oct 2026:**
- England v Pakistan at Birmingham, 3rd Test, 9–12 Sep 2026 (Test # 2635, match 1496584)
- England v Pakistan at Lord's, 2nd Test, 27–30 Aug 2026 (Test # 2634, match 1496583)
- Sri Lanka v India at Colombo (SSC), 2nd Test, 23–27 Aug 2026 (Test # 2633, match 1544002)

## 11. Existing MCP servers and tools
Searched: GitHub repositories and code, the official MCP Registry, Glama, mcp.so, LobeHub, the Apify Store and PyPI. npm search wasn't possible from the development machine, and Smithery's search page needs JavaScript.

**Tools that query Statsguru directly:**

| Project | Notes |
|---|---|
| Crawlora MCP (`Crawlora-org/crawlora-mcp`, MIT) | Hosted web-data server with thousands of tools, including 22 `cricinfo_*` tools. `cricinfo_stats` queries Statsguru (class, type, view, team, opposition, host, ground, player, season, dates, sort, up to 100 rows), but has no minimums or paging and needs numeric IDs. `cricinfo_records` reads record pages. The open-source part (`index.mjs`, about 80 lines) only forwards calls to Crawlora's paid API with an API key (2,000 free credits a month). |
| Apify `getascraper/espncricinfo-statsguru-scraper` | Paid ($0.47–0.62 per 1,000 rows) batch runs. Raw export or recent-form summaries; format, view, team, opposition, ground, up to 20 players, dates and one minimum. Returns the Statsguru query URL with each summary. |
| `Coder190721/CricketIQ-MultiAgent` | No licence. Reads a player's Statsguru page, finds players through Google results, and falls back to made-up data when parsing fails. |

**Other Cricinfo data (not Statsguru queries):**

| Project | Notes |
|---|---|
| `sa2812/cricinfo-mcp` | One tool: the series archive for a year. |
| Apify `apiguruu/espncricinfo-api-statsguru-scraper` | Paid ($3 per 1,000 results), about 9,300 runs. Live scores, scorecards, rankings and fixed stats lists; also sold on RapidAPI. |
| Other Apify actors | `automation-lab`, `crawlerbros`, `scrapix` and `zapticx` Cricinfo scrapers, all paid. |
| ESPN JSON APIs **(unverified)** | Some projects call `site.web.api.espn.com/apis/personalized/v2/scoreboard/header?sport=cricket` and `site.api.espn.com/apis/site/v2/sports/cricket/<league>/summary?event=<id>` (and `/playbyplay`): `asaraog/mcp-cricket`, `machina-sports/sports-skills`, `KarthiPrasaath05/cerebra-d11`, `vivek4012/IPL_MCP_RAG`. |

**Statsguru-like servers built on Cricsheet ball-by-ball data.** Their numbers can differ from Cricinfo's and older careers are missing, so Cricinfo links can't back them up.

| Project | Notes |
|---|---|
| `mavaali/cricket-mcp` | The most complete: 35 tools, DuckDB, about 21,000 matches and 10.9M deliveries. |
| `ankitksr/duckworth-mcp` | About 5M deliveries; downloads about 200 MB and takes about 15 minutes on first run. |
| `i-m-arul/cricketstudio-mcp` | About 60 tools for IPL, MLC, WPL and the T20 World Cup, from a bundled snapshot. |
| `ashu017/cricket-chat-mcp` | Cricsheet plus about 100 hand-copied Cricinfo career totals, each with a `source_url` and kept apart from calculated figures. Tells the model to point users to Statsguru when it lacks data. |

**Others:** `tarun7r/cricket-mcp-server` (Cricbuzz; the most-starred, at 14), `pipeworx-io/mcp-cricket` (CricAPI), `lacausecrypto/mcp-sports-hub` and `DanielTomaro13/sportsdata-mcp` (multi-sport), and many tutorials and demos.

**Official MCP Registry:** 8 entries for "cricket" (`com.followontours/cricket-travel`, `com.whensport/cricket-2026`, `io.github.asaraog/mcp-cricket`, `io.github.i-m-arul/cricketstudio-mcp`, `io.github.pipeworx-io/cricket`) and none for "cricinfo" or "statsguru".

**Libraries:** `cricguru` (Python, MIT, PyPI 2.1.2 from March 2025; Statsguru results as pandas tables), `cricpy` (PyPI 0.0.25.10, October 2023), and the R packages `cricketdata`, `statsguRu` and `cricketr`.

**Conclusion:** nothing free offers filtered Statsguru queries with minimums, name lookup, comparisons and proof links.

## 12. MCP protocol and Python SDK

**Specification 2026-07-28** (from the release-candidate post; final release 28 July 2026):
- **Stateless core:** the `initialize` handshake (SEP-2575) and `Mcp-Session-Id` sessions (SEP-2567) are removed. The protocol version and client info travel in `_meta` on every request, and `server/discover` returns capabilities. State across calls should use IDs that the model passes back (for example dataset IDs).
- **Server requests:** servers may only ask the client for something while handling a client request (SEP-2260). Multi-round-trip requests return an `InputRequiredResult` with a `requestState` (SEP-2322).
- **HTTP details:** Streamable HTTP requires `Mcp-Method` and `Mcp-Name` headers (SEP-2243); list results carry `ttlMs` and `cacheScope` (SEP-2549); W3C trace context goes in `_meta` (SEP-414).
- Raw Streamable HTTP requests to crickey with protocol version `2026-07-28` use `Accept: application/json, text/event-stream`, `Content-Type: application/json`, `Mcp-Protocol-Version: 2026-07-28` and `Mcp-Method`. `Mcp-Name` is used for routed methods such as `tools/call`, where it carries the tool name. Responses can be plain JSON or SSE frames with JSON in `data:` lines. Over HTTP, `initialize` returns 404 with JSON-RPC `-32601` and no `Mcp-Session-Id`, matching the stateless 2026-07-28 protocol.
- **Extensions** are first-class (SEP-2133), including MCP Apps (server-rendered UIs, SEP-1865) and Tasks (long-running work).
- **Also:** stricter authorization (for example RFC 9207 `iss` validation), a formal deprecation policy, JSON Schema 2020-12 for tool `inputSchema` and `outputSchema` (SEP-2106), and `structuredContent` that can be any JSON value.

**Transports:**
- **stdio:** newline-delimited JSON-RPC with a subprocess the client launches.
- **Streamable HTTP:** each message is a POST to one endpoint, answered with JSON or a per-request SSE stream; clients must accept both `application/json` and `text/event-stream`.
- Streamable HTTP servers must validate the `Origin` header (403 if invalid), should bind to 127.0.0.1 when running locally, and should implement authentication.

**Python SDK (`mcp` 2.3.0, released 2 Oct 2026):**
- **Defining a server:** `from mcp.server import MCPServer`; tools use `@mcp.tool()` and resources `@mcp.resource("scheme://{param}")`. Type hints become the schema.
- **stdio:** `mcp.run()` defaults to stdio. stdout carries the protocol, so logs must go to stderr.
- **Streamable HTTP:**
  - `mcp.run(transport="streamable-http")` listens on `127.0.0.1:8000` at path `/mcp` (`streamable_http_path`). Transport options go to `run()`, not the constructor.
  - `json_response=True` returns plain JSON and drops progress notifications; the default streams SSE, which carries them.
  - `stateless_http` only affects clients older than 2026-07-28; newer requests have no session anyway.
  - `mcp.streamable_http_app()` returns a Starlette app for uvicorn. `@mcp.custom_route("/health", methods=["GET"])` adds plain routes, which are never authenticated.
- **DNS-rebinding protection** is on by default:
  - Only Host `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>` is accepted, with a matching Origin if one is sent. Otherwise it returns 421 (bad Host) or 403 (bad Origin).
  - `transport_security=TransportSecuritySettings(allowed_hosts=[...], allowed_origins=[...])` changes the allowlist, and `"host:*"` matches any port.
  - Passing a non-localhost `host=` switches the default protection off without allowlisting anything.
- **Authentication:** `TokenVerifier` plus `AuthSettings` make the server an OAuth resource server (RFC 9728 metadata, 401 with `WWW-Authenticate`). stdio is never protected.
- **Multiple workers** need a shared `RequestStateSecurity(keys=[...])` and the same server name.
- **Client:** `from mcp import Client`, then `async with Client("http://localhost:8000/mcp") as client: await client.call_tool(...)`. `Client(mcp)` connects in memory for tests, skipping HTTP.
- **Command line (`mcp[cli]`):** `mcp dev` opens the MCP Inspector (needs npx), `mcp run` runs a server, and `mcp install` only supports Claude Desktop.

## 13. Client configuration

**GitHub Copilot CLI:**
- **Adding servers:** `copilot mcp add NAME -- COMMAND [ARGS]` for stdio, or `copilot mcp add --transport http NAME URL`. Options: `--env KEY=VALUE`, `--header "Name: value"`, `--tools` and `--timeout MS`. `/mcp add` does the same interactively.
- **Configuration:** `~/.copilot/mcp-config.json`, under `mcpServers`. Each entry has `type` (local, stdio or http), `command`, `args`, `env`, `url`, `headers` and `tools`.
- **Per project:** `.mcp.json` (anywhere from the working directory up to the repo root) and `.github/mcp.json`, loaded only in trusted folders. `.vscode/mcp.json` isn't read.
- **Discovery:** `/mcp search` (experimental) searches the GitHub MCP Registry.

**VS Code:**
- `.vscode/mcp.json` uses a top-level `servers` key; the portable `.mcp.json` and `~/.copilot/mcp-config.json` use `mcpServers`.
- Servers can be installed from the `@mcp` gallery in the Extensions view or with "MCP: Add Server", set up in dev containers through `customizations.vscode.mcp`, and reused from Claude Desktop's configuration (`chat.mcp.discovery.enabled`).

## 14. Distribution options
Researched before D24 was decided.

| Option | How users install | Notes |
|---|---|---|
| Package runners | `uvx` (PyPI), `npx` (npm) or `dnx` (NuGet) | Needs the runtime and a published package. |
| `uvx` from GitHub | A release wheel URL or `git+https://…` | Needs uv, plus git for git URLs. |
| Docker image | `docker run -i --rm …` | Needs Docker. |
| MCP Bundle (`.mcpb`) | Download and double-click | A zip with a `manifest.json`, built with `mcpb init` and `mcpb pack` (`npm install -g @anthropic-ai/mcpb`). Manifest 0.4 adds `server.type: "uv"`, where the host app manages Python and dependencies (plain Python bundles can't ship compiled packages such as pydantic). `user_config` fields appear as an install form. Mainly for Claude Desktop; the docs recommend Node.js for the fewest dependencies. |
| Copilot plugins | `copilot plugin install`, `/plugin install` or `enabledPlugins` | Agent Plugins 1.0: `plugin.json` with `$schema` `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`, `mcp.json` and `skills/` at the root, and Copilot-only parts under `com.github.copilot/`. |
| One-click install links | Buttons in a README | VS Code and Cursor. |
| Hosted remote server | Add a URL | Works with web chat apps, but needs public hosting. |

**MCP Registry** (in preview) lists `server.json` entries that point to packages, with ownership checks per package type:
- npm (registry.npmjs.org only): `mcpName` in `package.json`.
- PyPI (pypi.org only) and NuGet: `mcp-name: <server name>` in the README.
- Cargo: visible `mcp-name:` text, since crates.io strips HTML comments.
- OCI images on docker.io, ghcr.io, `*.pkg.dev`, `*.azurecr.io` or mcr.microsoft.com: the label `io.modelcontextprotocol.server.name`.
- MCP Bundles: files on GitHub or GitLab releases.
- Remote servers: a URL.

Plain git URLs can't be listed.

**Agent Skills** (the alternative to MCP):
- **Format (search summary):** a skill is a folder with `SKILL.md` plus optional `scripts/`, `references/` and `assets/`. The YAML front matter needs `name` (1–64 lowercase characters, matching the folder) and `description` (up to 1,024 characters); `license`, `compatibility`, `metadata` and `allowed-tools` are optional.
- **Loading (search summary):** agents read only names and descriptions at start, and load full instructions when a task matches.
- **Support (search summary):** Claude, GitHub Copilot, OpenAI Codex, Cursor, Gemini CLI and others.
- **Copilot:** loads project skills from `.github/skills`, `.claude/skills` or `.agents/skills`, and personal skills from `~/.copilot/skills` or `~/.agents/skills`. `gh skill` installs skills from GitHub repositories.

## 15. Versions and development environment
Latest stable releases, checked 3 Oct 2026. crickey uses these (D22); plan.md refers to this section instead of repeating them.

**Runtimes, tools and images:**

| Component | Version |
|---|---|
| Python | 3.14.8 (3.15 is still a release candidate) |
| uv | 0.12.23 |
| MCP specification | 2026-07-28 |
| Docker Engine | 29.8.2 |
| Docker base image | `python:3.14.8-slim-trixie` |
| GitHub Actions | `actions/checkout` v7, `astral-sh/setup-uv` v10, `docker/setup-qemu-action` v4, `docker/setup-buildx-action` v4, `docker/login-action` v4, `docker/metadata-action` v6, `docker/build-push-action` v7 |

**Python packages (PyPI):** D21 says which ones crickey uses; the rest were alternatives considered.

| Package | Version | Released | Notes |
|---|---|---|---|
| mcp | 2.3.0 | 2026-10-02 | Python 3.10+. Versions 2.1.0 and later support spec 2026-07-28; the 1.x line continues (1.30.0). |
| fastmcp | 4.0.10 | 2026-09-25 | |
| httpx | 0.28.1 | 2024-12-06 | |
| lxml | 6.1.3 | 2026-09-02 | |
| selectolax | 0.4.13 | 2026-09-29 | Python below 3.15 |
| pydantic | 2.13.5 | 2026-08-28 | |
| pandas | 3.0.6 | 2026-09-17 | Python 3.11+ |
| polars | 1.44.2 | 2026-09-09 | |
| duckdb | 1.5.6 | 2026-09-28 | |
| rapidfuzz | 3.14.6 | 2026-08-30 | Python 3.11+ |
| cachetools | 7.2.0 | | TLRUCache: LRU with a per-entry expiry function (ttu), size from getsizeof, injectable timer |
| hishel | 1.4.0 | 2026-09-16 | RFC 9111 HTTP cache; SQLite storage |
| respx | 0.23.1 | 2026-04-08 | |
| pytest | 9.1.1 | 2026-06-19 | |
| uvicorn | 0.54.0 | | |
| ruff | 0.16.10 | | |

Development machine: Windows with Python 3.14.6, uv 0.11.21, Node.js 24.21.0, Docker 29.5.2, git 2.55.0, gh 2.101.0 and curl 8.21.0.

Dockerfile frontend 1.10+ supports `env=` on secret mounts.

Dependabot's supported-ecosystems documentation lists `uv` as a Python ecosystem with version updates, and dependabot-core's uv file fetcher accepts a repository with `pyproject.toml` even when no `uv.lock` is committed ("Repo must contain a requirements.txt, uv.lock, requirements.in, or pyproject.toml").

## 16. Open questions
To check while building:
- Does `https://www.espncricinfo.com/ci/content/player/<id>.html` open the player's profile in a browser? Scripts get 403, so check by hand.
- How do `player_involve` IDs relate to player-page IDs (Babar Azam: 56880 and 348144)? Until that's known, each lookup needs the form's name search (R4).

**Answered on 4 Oct 2026** (details in the sections shown):
- httpx sending curl's User-Agent string gets 200 (R1).
- "Match involving players" and "Match involving captains" produce `player_involve` and `captain_involve`, with their own player IDs (R4).
- `qualmin` is compared with exact values, to at least 4 decimals (R2).
- Type-specific fields for every stat type (R4), and their minimum and sort options (R5).
- Team and trophy lists for classes 2, 3, 6 and 11, and the ODI World Cup's `trophy` value, 12 (R4). The league IDs in www URLs are the same as `trophy` values (R9).
- Player pages accept date ranges and `trophy` (R6).
- The query engine still refuses first-class queries (400 on 4 Oct), but record list pages load (R9).
- Classes 12, 13 and 16 (R3).
