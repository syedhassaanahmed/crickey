# Research notes

Facts about Statsguru and the tools crickey uses, so they don't need to be found again. Each was observed directly, unless marked:
- **(search summary)** marks facts from a web-search summary rather than the primary page.
- **(unverified)** marks facts seen in other people's code that we haven't tested.

Statsguru's figures, such as players' records and row counts, are as of 3–4 October 2026 unless an item gives another date.

[plan.md](plan.md) has the goal and how crickey is built, [evals/golden_questions.toml](../evals/golden_questions.toml) lists the golden questions, and [decisions.md](decisions.md) records what was decided from these facts, by number (D1, D2, …).

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
17. Evals with Inspect AI and Ollama

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
| httpx 0.28.1 sending `curl/8.21.0` | 200 |

`Mozilla/5.0` and a full Chrome-like User-Agent also got 200 on form, results and player pages.

### Rate limit
- On 1 Nov 2026, requests about 2.5 s apart with curl's User-Agent got 200 eleven times, then a 429.
- The same day, after 40 quiet minutes, crickey's default spacing (D9) sent 4 requests 3 s apart and a 5th at 15 s, and all 5 got 200. The largest burst Statsguru allows isn't known.

## 2. Statsguru URL model

| Page | URL |
|---|---|
| Filter form | `https://stats.cricinfo.com/ci/engine/stats/index.html?class=<class>;type=<type>` (add `filter=advanced` for the advanced form) |
| Results | The same parameters plus `template=results` |
| Player page | `https://stats.cricinfo.com/ci/engine/player/<id>.html?class=<class>;template=results;type=<type>[;view=<view>]` |
| Player search | `https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=<name>;template=analysis` |
| Records index | `https://stats.cricinfo.com/ci/engine/records/index.html?class=<class>` |

- **Separators:** parameters are separated by `;`, and `&` also works. Cricinfo redirects every query to a standard URL with parameters sorted alphabetically, for example `?class=2;orderby=hundreds;qualmin1=10;qualval1=hundreds;template=results;type=batting`.
- **Repeated keys:** checkbox groups are encoded as repeated keys. A live T20I team results query with `result=1;result=2;size=10` returned 200 and kept the standard URL as `class=3;result=1;result=2;size=10;template=results;type=team`. Only that already-sorted order was checked.
- **Repeated keys for dropdown filters:** Statsguru applies repeated keys as “one of” for each filter below, as each query's filter summary shows. Checked live with `size=10` and curl's User-Agent.

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

Not checked: `view`, `groupby`, `orderby`, `orderbyad`, `size` and `page` are output controls, not filters; quick-pick dropdowns such as `spanquickpick`, `agequickpick`, `runsquickpick`, `qualquickpick` and similar range shortcuts are not supported by crickey; text and numeric ranges are not dropdowns; checkbox groups take repeated keys anyway (above); radios (`toss`, `batting_fielding_first`, `captain`, `keeper`, `outs`) are not dropdowns and leaving them blank already means either state; `team_view` chooses whether team totals are from the batting or bowling side, so several values would not narrow to “one of”.
- **Links inside pages:** player profiles are `/ci/content/player/<id>.html`; matches are `/ci/engine/match/<id>.html`.
- **Date range:** `spanmin1=07+Sep+2016;spanmax1=24+Feb+2026;spanval1=span`, with dates as `DD Mon YYYY`. The form's hidden `spanmin0` and `spanmax0` hold the class's first and latest match dates. First match dates: Tests 15 Mar 1877, ODIs 05 Jan 1971, T20Is 17 Feb 2005, all T20 13 Jun 2003 and combined internationals 15 Mar 1877.
- **Minimums:** `qualval1=<field>;qualmin1=<n>`, with optional `qualmax1`. The form shows only one, but `qualval2`/`qualmin2` and `qualval3`/`qualmin3` also work: a query with three minimums returned only rows meeting all three.
- **A field in two minimums:** Statsguru doesn't read the same field in two qualifications as two minimums. On 8 Oct 2026, combined-internationals fielding with `qualval1=caught_fielder;qualmin1=100;qualval2=caught_fielder;qualmin2=139` returned 4 rows: JN Rhodes on 139 and three players on exactly 100. `caught_fielder>=139` alone returned 86 rows. So each field goes in one qualification, and a proof link tightens a field the query already qualifies instead of repeating it.
- **Maximums for lower-is-better bowling fields:** a `qualmax` keeps only rows at or below it. In a live check with curl's User-Agent on 5 Oct 2026, Test bowling in Asia with `wickets>=100` and `qualmax2=25;qualval2=bowling_average` returned 7 rows, all with displayed bowling average at or below 25. The same check with `qualmax2=50;qualval2=bowling_strike_rate` returned 5 rows, all with displayed bowling strike rate at or below 50. Because Statsguru compares exact values while it displays truncated values, answer proof links use an inclusive display maximum: `qualmax2=27.5199;qualval2=bowling_average` with `wickets>=92` included JM Anderson's displayed 27.51 in Asia.
- **Minimum precision:** minimums are compared with exact values, to at least 4 decimals. Babar Azam's T20I average is 38.94915…; `qualmin2=38.9491` kept his row, while `qualmin2=38.9492` returned "No records available to match this query". Fielding rates take decimal minimums too: on 8 Oct 2026, combined-internationals fielding with `dismissals>=100` and `qualmin2=0.411;qualval2=dismissals_per_inns` returned 172 rows, including JN Rhodes's displayed 0.411.
- **Sort:** `orderby=<field>`; `orderbyad=reverse` reverses the order.
- **Bowling sort order:** by default, bowling average, bowling strike rate and economy rate sort best (lowest) first, and count fields such as wickets, five-wicket hauls and ten-wicket matches sort highest first. On saved pages, `orderby=wickets` returns most wickets first, and `orderby=bowling_average;orderbyad=reverse` returns higher averages before lower averages; the reverse page for bowling strike rate has the same shape. A live `orderby=economy_rate` Test bowling query in Asia with `wickets>=100` returned lower economy rates first on 5 Oct 2026 (1.99, 2.01, 2.13...).
- **Fielding sort order**, checked live: every fielding field sorts best first by default. Count fields sort most first: `caught`, `caught_fielder` and `dismissals` in combined internationals, `stumped` in Tests (WAS Oldfield 52, TG Evans 46, then MS Dhoni and SMH Kirmani 38) and `caught_keeper` in ODIs (AC Gilchrist 417, MV Boucher 402, KC Sangakkara 383). `orderby=dismissals_per_inns` with Test `dismissals>=50` returned the highest rates first (TD Paine 2.308, Q de Kock 2.297, CO Browne 2.25).
- **Paging:** `size` is 10, 25, 50 (default), 100, 150 or 200, and `page=<n>` selects later pages. Each page shows the total, for example "Page 2 of 5 Showing 201 - 400 of 850", so the first page tells how many pages a query needs. A 200-row page is about 1 MB of HTML.
- **Truncated decimals:** averages and strike rates are cut off, not rounded. Babar Azam's T20I average is 4596 ÷ 118 = 38.949, shown as 38.94 (rounding would give 38.95). Bowling figures are truncated too (R8), and so is fielding's dismissals per innings (D/I): TD Paine's 157 ÷ 68 = 2.3088 shows as 2.308, and AT Carey's 210 ÷ 96 = 2.1875 as 2.187.
- **Trailing zeros** are dropped from displayed numbers, for example "47.8" and "55".
- **Fixed decimal places for proof thresholds:** saved pages show batting average, batting strike rate, bowling average and economy rate to at most 2 decimal places, bowling strike rate to at most 1 decimal place and fielding D/I to at most 3, with trailing zeros dropped. Proof links that need an inclusive displayed maximum use those fixed precisions rather than the number of decimals present in the cell text.

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

**The aggregate series view** (`type=aggregate;view=series`), checked on ODIs, with counts as of 29 Oct 2026:
- One row per series, ordered by start date, under the caption "Series averages". The columns are Series, Mat, Won, Tied, NR, Runs, Wkts, Balls, Ave, RPO, Start Date and Winner, which is "-" while the series is in progress.
- The Series column is blank. Each row's "investigate" menu has the series' ID, in its "Query for this series" link (`series=617`), and its home page, `/ci/content/series/60876.html`, which redirects to www.espncricinfo.com. The advanced form's series list, 983 for ODIs, has the names for those IDs: 617 is "Reliance World Cup, 1987/88" and 701 "Wills Triangular Series, 1994/95".
- The page's total ("Showing 1 - 11 of 11") is the number of series, and `tournament_type` takes repeated keys: `tournament_type=3;tournament_type=5` gives the series of three or more teams.
- A series counts when any of its matches meets the filters, and Mat and Start Date count only those matches. With `host=7`, 11 ODI series of three or more teams were in Pakistan, including the 1987 and 1996 World Cups, which it co-hosted; the 1996 World Cup's row has 16 matches, from 16 Feb 1996. With `team=7` instead, Pakistan played in 95, and the same row has its 6 matches, from 24 Feb 1996.
- The 2020–23 ODI Super League is one series of five or more teams.

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
  - There's no view by host country: `view=host` returns HTTP 400. The summary page's grouped rows have one row per host country instead (below).
- **Filters:** `opposition`, `host` and `ground` (lists specific to the player; Babar Azam's ODIs have 14 oppositions, 12 hosts and 51 grounds), `home_or_away`, dates (`spanmin1` and `spanmax1`), `spanquickpick`, `season` and `result` (1 won, 2 lost, 3 tied, 5 no result). There's no `team` filter, though a player with more than one team gets a grouped row for each (below).
- **Career span:** the player's form page for a format (the same URL without `template=results`, which robots.txt allows) has hidden `spanmin0` and `spanmax0` set to the player's first and last match dates in that format. Babar Azam's T20Is: 07 Sep 2016 to 24 Feb 2026; his ODIs: 31 May 2015 to 04 Jun 2026.
- **Format tabs** show each format's span. For Babar Azam: "Tests (2016/17 - 2026)", "ODIs (2015 - 2026)" and "T20Is (2016 - 2025/26)", plus an "other" menu with class 11 (All Test/ODI/T20I), 6 (Twenty20), 21 (Youth ODIs) and 22 (Youth T20Is).
- **Career summary columns:**
  - T20I batting: Span, Mat, Inns, NO, Runs, HS, Ave, BF, SR, 100, 50, 0, 4s, 6s.
  - T20I bowling: Span, Mat, Inns, Overs, Mdns, Runs, Wkts, BBI, Ave, Econ, SR, 4, 5.
  - Combined-internationals fielding: Span, Mat, Inns, Dis, Ct, St, Ct Wk, Ct Fi, MD, D/I, the same as the fielding results columns (R8).
  - The summary page also has grouped rows, split by a "Grouping" column (below).
- **Grouped rows**, from 13 saved summary pages: Younis Khan (43652) and Rahul Dravid (28114) in Tests; Imran Khan (40560) in Tests from 1981 to 1992 (two copies); Babar Azam's T20Is (all, and 2020 to 2021) and ODI World Cups; JM Anderson's Test bowling in Asia and in India or Pakistan; DW Steyn's and M Ntini's Test bowling in Asia; and V Kohli's and Shoaib Malik's T20I batting in the UAE.
  - After "Career averages", the summary page (no `view`) has a "Career summary" table. Its first column is "Grouping", and each row has the same columns as "Career averages". A blank one-cell row separates the groupings.
  - The groupings come in this order: the player's teams (only for a player with more than one, such as Dravid's "ICC World XI" and "India"), opposition ("v Australia"), host country ("in Australia", "in U.A.E."), continent ("in Africa", "in Asia"), home, away and neutral, year ("year 2000", with a blank Span), season ("season 1999/00"), then captain, toss, batting first, innings, result, series and position groupings, among others.
  - Host countries and continents share the "in " label. The link that ends each row ("view all innings for this row", with `view=innings`) names the grouping: `opposition=<team ID>`, `host=<host ID>`, `continent=<continent ID>`, `year=<year>`, `season=<season>` or `home_or_away=<1 to 3>`. Some groupings set two or three keys, such as "won batting first" (`batting_fielding_first=1;result=1`) and positions.
  - That link is the page's own query, as in the header's sort links, with the grouping's key set to the row's value. The row's value replaces a page filter on the same key: on Anderson's page filtered to `host=6;host=7`, "in India" links to `host=6`. On a page filtered to one value of that key, such as Malik's `host=27` page, the "in U.A.E." row's link is the page's own query.
  - Filters apply to the grouped rows. On every filtered page above (by dates, trophy, continent or host), the links carry the page's filters. The host, opposition, continent, year and season rows each add up to the filtered row's matches, for example 55 for Imran Khan from 1981 to 1992, whose year rows run from 1981 to 1992 only. On the unfiltered pages they add up to the overall row's matches: 118 for Younis Khan and 164 for Dravid.
  - Younis Khan's 11 host-country rows each show at least one hundred: 11 in the UAE, 7 in Pakistan, 3 each in Bangladesh, India and Sri Lanka, 2 in England, and 1 each in Australia, New Zealand, South Africa, the West Indies and Zimbabwe. All 10 of Dravid's do too. These two are golden question 8's known answers; other players can only be checked one at a time (D34).
- **Grouped rows on crickey's pages**, checked live: crickey's pinned dates (D16) make every player page it reads a filtered page, with "unfiltered" and "filtered" rows, and the grouped rows' links carry those dates.
  - `player_record(player_id=43652, format=Test, split_by=host)` took 1 request. Both rows show 118 Tests and 34 hundreds, and the 11 host-country rows add up to 118 matches, with the same hundreds as above.
  - `player_record(player_id=8608, format=Test, discipline=bowling, continent=Asia, split_by=host)` took 1 request. The filtered row shows 32 Tests and 92 wickets. The host rows are India 17, Pakistan 2, Sri Lanka 7 and U.A.E. 6, adding up to 32, with five-wicket hauls only in Sri Lanka (2). The "in Asia" row links to the page's own query.
- **Innings list (`view=innings`):** one row per match, after the same "Career averages" table, so one request gives both.
  - Batting columns: Runs, Mins, BF, 4s, 6s, SR, Pos, Dismissal, Inns, (blank), Opposition, Ground, Start Date, then the match label (for example "T20I # 566").
  - Bowling columns: Overs, Mdns, Runs, Wkts, Econ, Pos, Inns, (blank), Opposition, Ground, Start Date, then the match label.
- **Filters in the URL:** player pages accept advanced-form filters that their own form doesn't show, including dates and `trophy`. Babar Azam's T20I summary page links innings lists such as `…/player/348144.html?batting_fielding_first=1;class=3;result=1;template=results;type=batting;view=innings`.
- **Filtered pages:** with any filter, "Career averages" shows an "unfiltered" row and a "filtered" row (examples in R10).
- **Player-page repeated filters:** player pages also apply repeated host keys as "one of". In a live check with curl's User-Agent, JM Anderson's Test bowling page with `host=6;host=7` showed a filtered row.
- **Bowling player pages with continent filters** (golden question 6), checked live: JM Anderson (ID 8608) with `class=1;continent=2;type=bowling` showed the filtered Asia row as 32 Tests, 92 wickets, average 27.51, economy 2.59 and strike rate 63.6. DW Steyn (ID 47492) with the same filter showed 22 Tests, 92 wickets, average 24.11, economy 3.36 and strike rate 42.9.
- **Fielding player pages**, checked live with curl's User-Agent: JN Rhodes (ID 46973) with `class=11;opposition=6;type=fielding` and crickey's pinned dates showed "Career averages" with the fielding columns above. The unfiltered row was 297 matches, 338 innings and 139 catches, all as a fielder (D/I 0.411); the filtered row (v India) was 36 matches, 40 innings and 17 catches (D/I 0.425). The page also had the "Career summary" grouped rows (formats, opposition, host, continent and so on), as batting and bowling pages do.
- **Date-filtered player pages**, checked live: Imran Khan (ID 40560) with `class=1;spanmax1=31+Dec+1992;spanmin1=17+Sep+1981;spanval1=span;template=results;type=batting` showed the unfiltered row as 88 Tests, 3807 runs and average 37.69, and the filtered row as 55 Tests, 72 innings, 19 not outs, 2624 runs, HS 136, average 49.5, 5 hundreds and 16 fifties.
- **Profile link:** the summary row's "Profile" link is `/ci/content/player/<id>.html`. On stats.cricinfo.com it redirects (302) to `https://www.espncricinfo.com/ci/content/player/<id>.html`, part of the player's profile on www, which scripts can't fetch (R1).
- **Same IDs:** Statsguru and the www profile pages use the same player ID. For example, 348144 is Babar Azam on Statsguru and in `https://www.cricinfo.com/cricketers/babar-azam-348144`.
- **Player name:** the "Career averages" table doesn't name the player. The breadcrumb above the format tabs does: a link to `/ci/engine/player/<id>.html` reading "Statistics / Statsguru / <name> / <format>", for example "Statistics / Statsguru / Imran Khan / Test matches". The name is Statsguru's display name, as in results tables ("JM Anderson", "V Kohli"); the page's script variable `omniPageName` has the common name ("James Anderson") instead. Summary pages, form pages and innings lists all have the breadcrumb; for class 11 its format reads "Combined Test, ODI and T20I records".
- **A format the player never played**, checked live with curl's User-Agent: Imran Khan (ID 316363), who played Tests but no ODIs, with `class=2;template=results;type=batting` and crickey's pinned dates, returned HTTP 200 with his name in the breadcrumb and format tabs only for his own formats: on 7 Oct 2026, "Tests (2014/15 - 2019/20)", and "Twenty20 (2009 - 2024/25)" under "other". It had no "Career averages" table, only a "Career summary" table reading "No records available to match this query". His ODI form page (`class=2;type=batting`) has no `spanmin0` or `spanmax0`.

## 7. Player search
- `https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=babar;template=analysis` returned 15 people.
- Each row has the player's name (with the full name in brackets when the name uses initials) and country code, then one entry per format with its span and match count. Each entry links to `/ci/engine/player/<id>.html?class=<class>;type=allround`.
- Example row: "Babar Azam PAK", with "Test matches player (2016/17 - 2026, 66 matches)", "One-Day Internationals player (2015 - 2026, 143 matches)", "Twenty20 Internationals player (2016 - 2025/26, 145 matches)", "Combined Test, ODI and T20I player (2015 - 2026)", "Under-19s Youth One-Day Internationals player (2009/10 - 2012, 36 matches)", "Under-19s Youth Twenty20 Internationals player (2009/10, 1 match)" and "Twenty20 matches player (2012/13 - 2026, 359 matches)".
- Results mix international, domestic-only and youth players who share a name, for example Babar Hayat (Hong Kong), Muhammad Babar (Spain) and Zulfiqar Babar (Pakistan). Country codes can be combined ("BHM/PAK"), and other searches also return women and officials. Name lookups must filter by class, and ideally by country.
- Some players share exactly the same name and country, so only the ID tells them apart. On 6 Oct 2026, "Imran Khan" (PAK) returned two Test players: ID 40560 (1971 - 1991/92, 88 matches) and ID 316363 (2014/15 - 2019/20, 10 matches).

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
  - The same columns appear for ODIs, T20Is, all T20 and combined internationals; all T20 lists teams in a note row, as for batting.
  - `Dis` is `Ct` + `St`, and `Ct` is `Ct Wk` + `Ct Fi`: AB de Villiers in combined internationals has 480 = 463 + 17, and 463 = 215 + 248. Run-outs aren't counted anywhere in the table.
  - `D/I` is dismissals per innings fielded, truncated to 3 decimals (R2).
  - Combined-internationals rows include matches for composite teams such as the ICC World XI and Africa XI: JH Kallis has 338 catches as "(Afr/ICC/SA)", but 334 with `team=3`.
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
- Statsguru's query engine doesn't serve first-class or List A figures: a `class=4` results query returned 400 with an error page ("This page does not exist or has been moved"). Queries for `class=4` and `class=5` have also returned 503 while `class=6` returned 200.
- `https://stats.cricinfo.com/ci/engine/records/index.html` returned 200 and links classes 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 16, 20, 21 and 23 (labels in R3).
- `records/index.html?class=4` (title "Records | First-class matches | Cricinfo.com") returned 200. It links fixed record lists at `/ci/content/records/<id>.html` and team records at `/ci/engine/records/index.html?category=2;class=4`.
- Record lists are reachable, though `/ci/content/records/251072.html` has also returned 503. It's a table headed Player, Span, Mat, Inns, NO, Runs, HS, Ave, 100, 50, led by JB Hobbs (1905–1934) with 199 hundreds.
- The List A index (`records/index.html?class=5`) links 40 record lists, for example 282830 Most runs in career and 117937 Highest innings totals.

First-class record lists, 21 of the 44 the page links:

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

**Default-minimum count checks** (size=10 pages, fetched 4 Oct 2026):
- Hundreds minimums: Tests `hundreds>=5` 292 rows; ODIs `hundreds>=5` 144; T20Is `hundreds>=1` 191; all T20 `hundreds>=3` 121; combined internationals `hundreds>=10` 207.
- Innings minimums for innings per fifty-plus innings are too broad: Tests `innings>=20` 1225 rows; ODIs `innings>=20` 1060; T20Is `innings>=20` 1064; all T20 `innings>=30` 2347; combined internationals `innings>=30` 1965.
- Outs minimums for balls per dismissal are too broad: ODIs `outs>=20` 907 rows; T20Is `outs>=15` 1114; all T20 `outs>=25` 2167; combined internationals `outs>=40` 1279.

**Bowling default-minimum count checks** (size=10 pages, fetched 5 Oct 2026):
- Wickets floors: Tests `wickets>=100` 206 rows; ODIs `wickets>=100` 178; T20Is `wickets>=50` 236 (from the T20I bowling check below); all T20 `wickets>=100` 446; combined internationals `wickets>=200` 193.
- Filtered-floor counts, measured with only a recent 10-year span from 5 Oct 2016 to 5 Oct 2026: Tests `wickets>=30` 110 rows; ODIs `wickets>=30` 190; T20Is `wickets>=20` 681; all T20 `wickets>=30` 1188, so the filtered all T20 floor is `wickets>=50`, which gives 706 rows; combined internationals `wickets>=50` 382. Longer spans, such as a 20-year career, can return more rows than D10 allows; the tool then stops with its "too broad" message.

**Filtered count and comparison floors** (fetched 6 Oct 2026):
- Test bowling in Asia for England or South Africa (`team=1;team=3;continent=2`) with `wickets>=1`: 178 rows. JM Anderson and DW Steyn are tied first with 92 wickets (golden question 6; R6 has their full records), then MJ Leach 79, and GP Swann and DL Underwood 73 each.
- T20I batting in the UAE (`host=27`), all time: `runs>=1000` gives 4 rows. V Kohli (ID 253802) has 344 runs there in 8 innings with 3 not outs, average 68.8 and strike rate 134.9; `runs>=344` gives 43 rows.
- In the same table, Shoaib Malik (ID 42657) has 549 runs, average 27.45 and strike rate 122; `runs>=549` gives 18 rows, and adding `batting_average>=27.45` and `batting_strike_rate>=122` gives 7: Malik and the 6 batters who beat him on both.

**Fielding default-minimum count checks** (size=10 pages, fetched 8 Oct 2026):
- Unfiltered floors: Tests `dismissals>=50` 241 rows; ODIs `dismissals>=50` 201; T20Is `dismissals>=25` 301; all T20 `dismissals>=100` 206; combined internationals `dismissals>=100` 239. Every fielding count is at most a player's dismissals, so a table on any fielding count at the same floor is no bigger.
- Filtered-floor counts, with only a recent 10-year span from 8 Oct 2016 to 8 Oct 2026: Tests `dismissals>=20` 110 rows; ODIs `dismissals>=20` 161; T20Is `dismissals>=10` 931, too broad for D10, and `dismissals>=20` 374; all T20 `dismissals>=50` 375; combined internationals `dismissals>=30` 372. Over a 24-year span, 15 Nov 1989 to 16 Nov 2013 (about SR Tendulkar's career), combined internationals `dismissals>=30` gives 339 rows, still 2 pages.

**Jonty Rhodes (ID 46973), combined-internationals fielding** (golden question 7, fetched 8 Oct 2026): 297 matches, 338 innings and 139 catches, all as a fielder, with D/I 0.411.
- `caught_fielder>=140` gives 85 rows (Jayawardene 440, Ponting 364, Taylor 354, SPD Smith 350 and Kohli 345 lead), and `caught>=140` gives 146, counting wicketkeepers' catches.
- With `team=3` and `caught_fielder>=100`, he is 12th of 15 South Africans.
- `dismissals>=100` gives 239 rows (2 pages of 200); adding `dismissals_per_inns>=0.411` gives 172: Rhodes and the 171 with a higher rate.
- These tables have no date range, so they compare whole careers. That count keeps growing as current players pass 139, so the eval case for golden question 7 compares over his career span instead (below).

**Wasim Akram (ID 43547), Waqar Younis (ID 43543) and Imran Khan (ID 40560) bowling against India** (golden question 9, fetched 7 Oct 2026 through crickey's tools):
- Tests: Imran Khan 23 Tests, 94 wickets, average 24.04, strike rate 54, economy 2.67, best 8/60; Wasim Akram 12 Tests, 45 wickets, average 28.86, strike rate 65, economy 2.66, best 5/96; Waqar Younis 4 Tests, 8 wickets, average 48.75, strike rate 80.2, economy 3.64, best 4/80. All three are in Pakistan's Test bowling table against India (103 bowlers), where Imran Khan has the most wickets and Wasim Akram the second most: `https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;opposition=6;orderby=wickets;size=25;spanmax1=07+Oct+2026;spanmin1=15+Mar+1877;spanval1=span;team=7;template=results;type=bowling`
- ODIs: Wasim Akram 48 ODIs, 60 wickets, average 25.15, strike rate 40.4, economy 3.73, best 4/35; Waqar Younis 26 ODIs, 37 wickets, average 24.48, strike rate 31.7, economy 4.62, best 5/31; Imran Khan 29 ODIs, 35 wickets, average 22.25, strike rate 34.6, economy 3.85, best 6/14. Each is the player's ODI bowling page with `opposition=6`, for example `https://stats.cricinfo.com/ci/engine/player/43547.html?class=2;opposition=6;spanmax1=07+Oct+2026;spanmin1=05+Jan+1971;spanval1=span;template=results;type=bowling`
- The golden question names no format, so the answer covers Tests and ODIs, as the eval's question asks. In Tests, "Imran Khan" needs his ID, because two Pakistan Test players share the name (R7). In ODIs, the name resolves to 40560 directly, because the other Imran Khan (316363) played no ODIs.

**The eval cases' known answers** (checked live on 9 Oct 2026 through crickey's tools, 26 requests 15 s apart). [evals/golden_questions.toml](../evals/golden_questions.toml) has each case's question and the facts an answer must contain (D37); these are the checks behind them.
- **Golden question 1**, ODIs from 1 Jan 2000 to 31 Dec 2009, at least 10 hundreds (`leaderboard`, innings per hundred, 1 request): the qualifying group's figure is 13.72 innings per hundred. SR Tendulkar leads with 9.86 (21 hundreds), then RT Ponting 10, CH Gayle 10.16, ME Trescothick 10.17 and HH Gibbs 10.47.
- **Golden question 2**, Test hundreds more often than SR Tendulkar (ID 35320) over his career span, 15 Nov 1989 to 14 Nov 2013, with the default floor of 5 hundreds (2 requests): 6 players beat his 6.45 innings per hundred: CA Pujara 4.8, KC Sangakkara 6.06, ML Hayden 6.13, HM Amla 6.15, JH Kallis 6.3 and M Azharuddin 6.44.
- **Golden question 3**, T20I average and strike rate against BB McCullum (ID 37737) over his span, 17 Feb 2005 to 23 Jun 2015, with D29's floor of 1000 runs (3 requests): McCullum has 35.66 and 136.21, and 2 batters beat both: KP Pietersen (37.93, 141.51) and AD Hales (37.55, 137.84).
- **Golden question 4**, R Dravid (ID 28114) in Tests over the last 3 years of his career, 24 Jan 2009 to 24 Jan 2012 (2 requests): 33 matches, 59 innings, 2779 runs, average 52.43 and 10 hundreds.
- **Golden question 5**, SR Tendulkar in ODI World Cups (`trophy=12`, 1 request): 45 matches from 1992 to 2011, 44 innings, 2278 runs, average 56.95, 6 hundreds and 15 fifties.
- **Golden questions 6 and 8** match R6: JM Anderson and DW Steyn (ID 47492) in Asia in Tests, and Younis Khan's 11 and Dravid's 10 host countries, each with a hundred (6 requests, with a player search).
- **Golden question 7**, catches across all internationals against JN Rhodes over his span, 26 Feb 1992 to 12 Feb 2003, with D35's filtered floor of 30 (5 requests):
  - Counting wicketkeepers' catches, 26 players have more than his 139, led by wicketkeepers MV Boucher (417), IA Healy (379) and AC Gilchrist (369), and G Kirsten is level.
  - As a fielder, 12 have more: ME Waugh 256, SP Fleming 205, BC Lara 191, M Azharuddin 175, CL Hooper 174, MA Taylor 166, SK Warne 165, R Dravid 163, Inzamam-ul-Haq 149, and ST Jayasuriya, SR Tendulkar and SR Waugh 142 each. These 12 are on both lists, so they're the case's facts and either reading passes.
- **Golden question 9** matches the figures above for Tests and ODIs (6 requests).
- **Golden question 10**, ODI series of three or more teams in Pakistan from 1 Jan 1990 to 31 Dec 1999 (`series_count`, checked live on 29 Oct 2026, 2 requests: the series view and the form's series list): 3 series: the Wills Triangular Series, 1994/95, from 12 Oct 1994 (9 matches, won by Australia); the Wills World Cup, 1995/96, from 16 Feb 1996 (its 16 matches in Pakistan, won by Sri Lanka); and the Wills Quadrangular Tournament, 1997/98, from 1 Nov 1997 (7 matches, won by South Africa). All time, there were 11 by 29 Oct 2026 (R4).

**The held-out cases' known answers** (checked live on 29 Oct 2026 through crickey's tools, 40 requests 15 s apart). [evals/golden_questions.toml](../evals/golden_questions.toml) has each case (D45).
- **Held-out 1**, Tests from 1 Jan 1980 to 31 Dec 1999, at least 15 hundreds (`leaderboard`, innings per hundred, 1 request): SR Tendulkar leads with 5.23 innings per hundred (22 hundreds), then M Azharuddin 6.9 and MD Crowe 7.71. The group's figure is 8.95.
- **Held-out 2**, ODI hundreds more often than Saeed Anwar (ID 42605) over his span, 1 Jan 1989 to 4 Mar 2003, with the default floor of 5 hundreds (3 requests): SR Tendulkar 8.82, HH Gibbs 10.25 and SC Ganguly 10.8 beat his 12.2 innings per hundred, and V Sehwag is level.
- **Held-out 3**, ODI average and strike rate against MEK Hussey (ID 5939) over his span, 1 Feb 2004 to 3 Sep 2012, with D29's floor of 1000 runs (4 requests): Hussey has 48.15 and 87.16, and 4 batters beat both: RN ten Doeschate (67, 87.7), HM Amla (57.75, 91.76), MS Dhoni (51.17, 88.42) and AB de Villiers (48.5, 93.12).
- **Held-out 4**, KC Sangakkara (ID 50710) in ODIs over the last 4 years of his career, 18 Mar 2011 to 18 Mar 2015 (3 requests): 117 matches, 111 innings, 5283 runs, average 52.3 and 15 hundreds.
- **Held-out 5**, SC Ganguly (ID 28779) in the ICC Champions Trophy (`trophy=44`, 2 requests): 13 matches from 1998 to 2004, 665 runs, average 73.88 and 3 hundreds. The case says "ICC Champions Trophy", because "Champions Trophy" alone also names the Sharjah Champions Trophy.
- **Held-out 6**, SK Warne (ID 8166) and A Kumble (ID 30176) bowling in Tests in Africa (`continent=1`, 4 requests): Warne 13 Tests, 67 wickets, average 24.17 and strike rate 59.7; Kumble 16 Tests, 59 wickets, 31.33 and 80.3.
- **Held-out 7**, catches across all internationals against RT Ponting (ID 7133) over his span, 15 Feb 1995 to 30 Nov 2012, with D35's filtered floor of 30 (8 requests): counting wicketkeepers' catches, 8 players have more than his 364, led by MV Boucher (953) and AC Gilchrist (813). As a fielder, only DPMD Jayawardene (397) has more, and he is on both lists.
- **Held-out 8**, BC Lara (ID 52337) in Tests by host country (2 requests): a hundred in 8 of his 9 host countries, all but India. The question asks yes or no, so the case's facts are Lara and India: a right answer may give the count as "8 of his 9" or "eight of them", or none, as in "every country except India".
- **Held-out 9**, AA Donald (ID 44716), SM Pollock (ID 46774) and M Ntini (ID 46592) bowling against the West Indies in Tests (6 requests): Donald 10 Tests, 43 wickets, average 20.79 and strike rate 43.1; Pollock 16 Tests, 70 wickets, 22.95 and 57.3; Ntini 15 Tests, 63 wickets, 27.58 and 50.7.
- **Held-out 10**, ODI series of three or more teams in Sri Lanka from 1 Jan 2000 to 31 Dec 2009 (`series_count`, 2 requests): 8, from the Singer Triangular Series, 2000 to the Compaq Cup, 2009, including the ICC Champions Trophy, 2002/03, which India and Sri Lanka shared.

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
Searched as of 3–4 Oct 2026: GitHub repositories and code, the official MCP Registry, Glama, mcp.so, LobeHub, the Apify Store and PyPI. Not searched: npm, and Smithery, whose search page needs JavaScript.

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
- **Server instructions:** Copilot CLI puts an MCP server's instructions in the system prompt only for allowlisted servers, unless it's started with `--allow-all-mcp-server-instructions`, which includes every server's. No `copilot config` setting, environment variable or `copilot mcp add` option does the same (Copilot CLI 1.0.105).
  - **In the system prompt:** each session's `system.message` event, in `~/.copilot/session-state/<session>/events.jsonl`, holds its system prompt. With the flag, all six lines of crickey's instructions were there word for word, in a `<crickey-*>` block among the tools' guidelines; without it, none were.
  - **Checked** with the v0.4.0 image and `gpt-6.1-sol`: golden questions 1, 4, 6, 9 and 10, each in a fresh `copilot -p` session with only crickey's tools, scored with the eval's checks. With or without the flag, each question made the calls the eval expects. 19 Statsguru requests, with no blocks.
  - **By default,** 2 of the 5 passed every check. For golden questions 6 and 9, the model cited the player pages without their pinned dates, `spanmin1` and `spanmax1` (D16), so the proof check found links no tool returned. For golden question 1, it named who led instead of the group's figure the question asks for.
  - **With the flag,** all 5 passed.

**VS Code:**
- `.vscode/mcp.json` uses a top-level `servers` key; the portable `.mcp.json` and `~/.copilot/mcp-config.json` use `mcpServers`.
- Servers can be installed from the `@mcp` gallery in the Extensions view or with "MCP: Add Server", set up in dev containers through `customizations.vscode.mcp`, and reused from Claude Desktop's configuration (`chat.mcp.discovery.enabled`).

## 14. Distribution options
The options D24 chose from.

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
crickey uses these versions (D22), each the latest stable release when it was checked. plan.md refers to this section instead of repeating them.

**Runtimes, tools and images:**

| Component | Version |
|---|---|
| Python | 3.15.0 |
| uv | 0.13.6 |
| MCP specification | 2026-07-28 |
| Docker Engine | 29.8.2 |
| Docker base image | `python:3.15.0-slim-trixie` |
| GitHub Actions | `actions/checkout` v7, `astral-sh/setup-uv` v10, `docker/setup-qemu-action` v4, `docker/setup-buildx-action` v4, `docker/login-action` v4, `docker/metadata-action` v6, `docker/build-push-action` v7 |

**Python packages (PyPI):** D21 says which ones crickey uses, and D39 the evals'; the rest were alternatives considered.

| Package | Version | Released | Notes |
|---|---|---|---|
| mcp | 2.3.0 | 2026-10-02 | Python 3.10+. Versions 2.1.0 and later support spec 2026-07-28; the 1.x line continues (1.30.0). |
| fastmcp | 4.0.10 | 2026-09-25 | |
| httpx | 0.28.1 | 2024-12-06 | |
| lxml | 6.1.3 | 2026-09-02 | |
| selectolax | 0.4.13 | 2026-09-29 | Python below 3.15 |
| pydantic | 2.14.1 | 2026-10-11 | 2.14.0 is the first release that supports Python 3.15; 2.13 pins pydantic-core 2.46, which has no 3.15 wheels. |
| pandas | 3.0.6 | 2026-09-17 | Python 3.11+ |
| polars | 1.44.2 | 2026-09-09 | |
| duckdb | 1.5.6 | 2026-09-28 | |
| rapidfuzz | 3.14.6 | 2026-08-30 | Python 3.11+ |
| cachetools | 7.2.0 | | TLRUCache: LRU with a per-entry expiry function (ttu), size from getsizeof, injectable timer |
| hishel | 1.4.0 | 2026-09-16 | RFC 9111 HTTP cache; SQLite storage |
| respx | 0.23.1 | 2026-04-08 | |
| pytest | 9.1.1 | 2026-06-19 | |
| uvicorn | 0.54.0 | | |
| ruff | 0.17.1 | 2026-10-15 | 0.17.0 is the first release that fully supports a `py315` target; 0.16 calls it a preview. |
| inspect-ai | 0.3.277 | 2026-10-06 | MIT; Python 3.10+. The evals' framework (R17). |
| openai | 3.26.1 | 2026-10-08 | Inspect's `ollama` and other OpenAI-compatible providers need it. |

**Development environment:**
- Development and the evals run on Windows, Linux (including WSL) and macOS on Apple Silicon. On Apple Silicon, the evals in R17 ran with inspect-ai 0.3.285 and openai 3.35.1.
- On macOS, Apple clang 21.0.0 builds the two eval dependencies that have no Python 3.15 wheels (R17): zstandard 0.25.0 with its C extension, and PyYAML 6.0.3 without libyaml.
- uv takes another package index from `UV_DEFAULT_INDEX` (D23). An index other than PyPI may not have the newest releases yet (D27, D39), and some serve a wheel's `.metadata` only after the wheel's first download through them, so uv can fail with a 404 on the metadata of a wheel nobody has fetched yet; downloading the wheel once fixes it.
- Dockerfile frontend 1.10+ supports `env=` on secret mounts.

Dependabot's supported-ecosystems documentation lists `uv` as a Python ecosystem with version updates, and dependabot-core's uv file fetcher accepts a repository with `pyproject.toml` even when no `uv.lock` is committed ("Repo must contain a requirements.txt, uv.lock, requirements.in, or pyproject.toml").

## 16. Open questions
- Does `https://www.espncricinfo.com/ci/content/player/<id>.html` open the player's profile in a browser? Scripts get 403, so check by hand.
- How do `player_involve` IDs relate to player-page IDs (Babar Azam: 56880 and 348144)? Until that's known, an involve ID comes only from the form's name search (R4).

## 17. Evals with Inspect AI and Ollama
[evals/README.md](../evals/README.md) says how to run the evals (D37–D40). In this section, an answer passes when it passes every check in plan.md › Evals, and "the scored run" is the run of all 20 cases in "Scores".

### Framework
- **[Inspect AI](https://inspect.aisi.org.uk)** (MIT, from the UK AI Security Institute; R15 has the version):
  - Its ReAct agent uses an MCP server's tools, over [Streamable HTTP or stdio](https://inspect.aisi.org.uk/tools-mcp.html).
  - It has model providers for Ollama (`ollama/<model>`, through Ollama's OpenAI-compatible API at `http://localhost:11434/v1`), any OpenAI-compatible API, Microsoft Foundry, OpenRouter, Anthropic and Google, so one task runs against many models.
  - Its scorers see the whole conversation, and epochs repeat each question.
  - It writes logs as `eval` or JSON (`--log-format json`). `inspect view` opens them, and `inspect view bundle` turns them into a static site, which needs a web server with HTTP range requests (Python's `http.server` has none).
  - It installs next to crickey's dependencies, `mcp` 2.x included, without conflicts: about 50 packages.
  - **On Python 3.15**, two of its dependencies have no wheels yet: zstandard (latest 0.25.0) and PyYAML (latest 6.0.3), both from September 2025. So installing the `evals` group builds them from source, which needs a C compiler. In `python:3.15.0-trixie`, with gcc, both build, zstandard with its C extension and PyYAML with libyaml. On Windows, both fail without Microsoft C++ Build Tools. ijson 3.6.0 has 3.15 wheels, and ijson 3.5.1's source builds without a compiler, in pure Python.
  - **In a Linux container**, the eval runs without Build Tools: in `python:3.15.0-trixie` with uv, the checkout mounted and `UV_PROJECT_ENVIRONMENT` outside it. crickey has to run inside the same container, because the SDK's Host check (D20) turns away `host.docker.internal`. Ollama on the host answers at `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`, and Statsguru answers the container's requests as it does the host's.
- **Alternatives:** pydantic-evals with Pydantic AI (MIT) is lighter, but the agent wiring, the model matrix and a log viewer would be ours to build, and its viewer, Logfire, is hosted. DeepEval (Apache-2.0) is pytest-native, but its MCP metrics need an LLM judge and its dashboards are in Confident AI's cloud. promptfoo has strong MCP and CI support, but it's Node.js.
- **Models in CI:** GitHub Models [was retired on 30 Jul 2026](https://github.blog/changelog/2026-07-01-github-models-is-being-fully-retired-on-july-30-2026/), and Copilot spends AI credits ([$0.01 each since June 2026](https://docs.github.com/en/billing/concepts/product-billing/github-copilot-billing)).

### Inspect 0.3.275 in detail
Read in its source.
- **`react(submit=False)`** ends when the model replies without calling a tool, as MCP clients do. Its default prompt asks the model to submit an answer, so the eval passes its own `AgentPrompt`.
- **MCP tools (`mcp_server_http`):**
  - Results reach the model as their `content` only; `structuredContent` and `_meta` are dropped.
  - The client handles no log or progress notifications, and its HTTP `timeout` defaults to 5 s.
  - Tool schemas go through Inspect's `ToolParams`, whose `ToolParam` keeps `type`, `format`, `description`, `default`, `enum`, `items`, `properties`, `additionalProperties`, `anyOf`, `required`, `pattern`, `minLength`, `maxLength`, `minimum`, `maximum` and `examples`. Everything else is dropped, including `$defs`, `$ref`, `oneOf` and `const`. So `query_stats`'s `query`, whose fields are under `$defs`, keeps only its description, and the tool's schema goes from 17,980 characters of JSON to 489.
  - Every parameter needs a description; Inspect's MCP tools fall back to the parameter's name.
- **Tool output** over 16 KiB is cut unless `max_tool_output` is raised.
- **Tool arguments:** a tool function gets a call's arguments as they are only through a parameter declared `**kwargs: Any`; otherwise Inspect binds them to the function's named parameters.
- **Timeouts:** Inspect's HTTP clients give up on a request after 600 s, unless `INSPECT_HTTP_REQUEST_TIMEOUT` says otherwise; the `ollama` provider doesn't take the `client_timeout` model argument that some others do. The clients are created before Inspect loads the task file, so a task can't change that. `GenerateConfig.timeout` only bounds a model call with its retries.
- **Concurrency:** with `max_connections` set in the task's config and no `max_samples`, samples run `max_connections` at a time.
- **Per sample:** `TaskState.token_usage` gives the tokens used, and `store()` is the sample's own store, also inside tools.
- **The MCP SDK** adds its own `_meta` key, `io.modelcontextprotocol/serverInfo`, to tool results, next to crickey's (D38).

### Ollama
- **Starting it:** variables set in front of `ollama serve` reach its server: with `OLLAMA_CONTEXT_LENGTH=65536`, its config lists `OLLAMA_CONTEXT_LENGTH:65536`, and `/api/ps` shows the model at a 65,536-token context. In PowerShell 7, `$env:` variables reach the programs it starts. Without `OLLAMA_NUM_PARALLEL`, the server's config shows it as 1.
- **Next to the app:** while the Ollama app runs, a second `ollama serve` on its port stops with "bind: address already in use". One on another port, such as `OLLAMA_HOST=127.0.0.1:11435`, reads the same model store as the app.
- **Context:** `ollama serve --help` gives the default context as 4k, 32k or 256k tokens, by VRAM. Under 24 GiB of VRAM it's 4k, too small for crickey's tools, and with 28.1 GiB it's 32k. Ollama reserves memory for the whole context when it loads a model ("On a CPU" below).
- **First prompt:** crickey's tool definitions, its server instructions and a question come to about 8,000 tokens through the eval's bridge (D40). With crickey 0.4.0 and Ollama 0.40.12, the first request for golden question 1 is 8,178 tokens for `qwen3:4b-instruct`, 8,298 for `qwen3.8:27b` and 2,386 for `gpt-oss:20b`, whose renderer leaves out object parameters' properties ("Models" below). The parameter descriptions add about 9% to the tool definitions: 9.7% as `qwen3:4b-instruct` counts them in Ollama 0.40.6, and 8.2% of `tools/list`'s JSON in tiktoken's `o200k_base`.
- **Prompt reuse:** Ollama keeps the last prompt it read, so a call that starts the same way only reads what's new. With one question at a time, the next question's first call reads little more than the question: in the scored run, `qwen3:4b-instruct` read 28 new tokens for golden question 2 and reused 8,140.
- **Prompt cache:** Ollama 0.40's llama-server also saves the previous conversation whenever a new one starts, up to 8 GiB or one context's worth of tokens (`--cache-ram`, default 8192 MiB). `LLAMA_ARG_CACHE_RAM=0` in Ollama's environment reaches llama-server and turns it off.
- **Cache type:** the KV cache is f16 by default. An 8-bit cache (`OLLAMA_KV_CACHE_TYPE=q8_0`) takes less memory, but reads prompts about 4.6 times slower on a CPU ("On a CPU" below).
- **Flash attention:** on a CPU, Ollama's default lets llama.cpp turn it on. Forcing it off (`OLLAMA_FLASH_ATTENTION=0`) needs an extra compute buffer of about 18 GB at 64k.
- **Runaway replies:** Ollama starts llama-server with `--context-shift`, which lets a reply run on past the end of the context, so a reply that never ends stops only at Inspect's one-hour model timeout, as an error. Small models write such replies: `qwen3:4b-instruct` wrote one for 23 minutes on a laptop CPU (4,550 tokens) until the run was stopped, and one for 14 minutes and over 39,000 tokens on Apple Silicon; `phi4-mini:3.8b` wrote 22,600 tokens in one reply. `--max-tokens 2048` bounds a reply to about 10 minutes on a CPU and leaves room for any answer or tool call. `inspect ctl sample cancel … --action score`, from another terminal, ends a reply and scores it as it stands.
- **Sleep** freezes a reply midway: llama-server timed one reply at 312 s that Ollama's handler, whose clock stops during sleep, timed at 2.4 s. On macOS, `caffeinate -i` in front of the eval's command keeps the Mac awake until the eval ends: `pmset -g assertions` lists caffeinate's `PreventUserIdleSystemSleep` while the eval runs, and not after. Closing the lid sleeps anyway. Ctrl+C stops a run and keeps the finished samples in the log, and `inspect eval-retry` asks the rest.

### On a CPU
Measured on a laptop CPU with 14 cores, 32 GB of RAM and no discrete GPU, with Ollama 0.40.1, `qwen3:4b-instruct` and an 11,236-token prompt of crickey's tools, its server instructions and a question.
- **Memory**, which Ollama reserves for the whole context when it loads the model:

  | Context | Default cache | 8-bit cache (`OLLAMA_KV_CACHE_TYPE=q8_0`) |
  |---|---|---|
  | 32k | 7.6 GB | 5.3 GB |
  | 64k | 12.8 GB | 8.2 GB |
  | 128k | about 23 GB (estimated) | 13.6 GB |
  | 256k | about 42 GB (out of memory) | about 24 GB (estimated) |

- **Reading:** the prompt took 5.5 minutes (34.3 tokens/s) with the default cache, and 25 minutes (7.5 tokens/s) with the 8-bit cache.
- **Writing:** 3–4 tokens a second with that prompt in context, and about 35 with a short one.

### On GitHub's runners
The largest free runners for a public repo have 4 CPUs and 16 GB; bigger runners and GPUs are paid, and the M1 macOS runner has 7 GB. A job on them can run for at most 6 hours. With Ollama 0.40.2 ([run](https://github.com/syedhassaanahmed/crickey/actions/runs/37889876851)), `qwen3:4b-instruct` read the 11,236-token prompt at 32k:

| Runner | CPU | Reading | Writing | Memory at 32k / 48k / 64k (left free) |
|---|---|---|---|---|
| `ubuntu-24.04` | AMD EPYC 7763: 2 cores, 2 threads each, AVX2 | 15 min (12.2 tokens/s) | 1.7 tokens/s | 7.6 / 10.3 / 12.8 GB (7.5 / 5.4 / 3.2 GiB) |
| `ubuntu-24.04-arm` | Neoverse-N2: 4 cores | 45 min (4.2 tokens/s) | 3.8 tokens/s | 7.9 / 10.6 / 13.1 GB (7.3 / 5.2 / 2.9 GiB) |

### On Apple Silicon
Measured on an Apple Silicon laptop with 36 GB of unified memory, where Metal lets the GPU use 28.1 GiB, so Ollama's default context there is 32k.
- **Two engines:**
  - GGUF models run in llama-server, as on a CPU.
  - Where a model's default tag has an MLX build, `ollama pull` fetches that on Apple Silicon, and Ollama runs it in its MLX engine (MLX 0.32): `qwen3.8:27b`, `gemma4:31b` and `nemotron-3.5-lightning:30b`, all nvfp4. The engine drafts tokens ahead (`qwen3.8:27b` keeps about 80% of its drafts) and keeps earlier prompts in a prefix cache that grows during a run. `LLAMA_ARG_CACHE_RAM` doesn't apply to it.
- **Per model**, with Ollama 0.40.11 at 64k, over golden questions 1–9 at 3 epochs:

  | Model | Engine, weights | GPU memory | Writing (tokens/s) | Seconds per question |
  |---|---|---|---|---|
  | `gpt-oss:20b` | llama-server, MXFP4 | 13.6 GiB | 86 | 59 |
  | `qwen3.8:27b` | MLX, nvfp4 | 26.0 GiB held, 28.5 peak | 48 | 19 |
  | `gemma4:31b` | MLX, nvfp4 | 28.0 GiB held, 30.9 peak | 30 | 78 |
  | `muse-glimmer:30b` | llama-server, Q4_K_M | 16.0 GiB | 21 | 129 |
  | `nemotron-3.5-lightning:30b` | MLX, nvfp4 | 23.9 GiB held, 25.4 peak | 112 | 14 |
  | `mistral-small3.2:24b` | llama-server, Q4_K_M | 23.4 GiB, 10.0 of it cache | 24 | 22 |
  | `glm-4.7-flash` | llama-server, Q4_K_M | 21.1 GiB | 42 | 25 |
  | `granite4.1:30b`, at 32k | llama-server, Q4_K_M | 24.5 GiB, 8.0 of it cache | 18 | 43 |
  | `deepseek-r1:8b` | llama-server, Q4_K_M | 14.2 GiB | 68 | 24 |
  | `phi4-mini:3.8b` | llama-server, Q4_K_M | 10.8 GiB | 94 | 16 |
  | `lfm2.5:24b` | llama-server, Q4_K_M | 15.0 GiB | 136 | 62 |
  | `olmo-3.1:32b-instruct` | llama-server, Q4_K_M | 23.1 GiB | 17 | 15 |

  Writing is the median over replies of at least 200 tokens, from Inspect's logs. Seconds include 15 s between every two Statsguru requests: `gpt-oss:20b`, the first model, sent 62 and `muse-glimmer:30b` 44; the rest found most pages in crickey's cache.
- **Too big for 64k:** `granite4.1:30b` needs 32.7 GiB at 64k, 16 GiB of it cache. llama.cpp keeps the context and puts 11 of its 65 layers on the CPU, and with macOS swapping it writes 2.4 tokens/s, so it runs at 32k.
- **Memory pressure:** `gemma4:31b` peaks at 30.9 GiB, above Metal's 28.1 GiB: macOS compressed and swapped about 5 GB, and its questions took 78 s each, against 19 s for `qwen3.8:27b`.
- **`qwen3.8:27b`**, with Ollama 0.40.12 over all 20 cases at 1 epoch: macOS's `footprint` gives its runner 20 GB once the model has loaded, 26 GB by the end, and a peak of 29 GB.

### Models
- **Thinking:**
  - `qwen3:4b` is `qwen3:4b-thinking-2507` (the same weights), which always thinks: `think: false` or `reasoning_effort: "none"` only moves its reasoning into the answer. `qwen3:4b-instruct` is `qwen3:4b-instruct-2507`, which doesn't think.
  - `qwen3.8:27b` thinks before every reply, but briefly: in the scored run, all 126 of its replies began with reasoning, a median of 253 characters and at most 1,709.
- **Renderers:** Ollama turns tool schemas into each model's own format, and two of these drop parts of an object parameter (Ollama 0.40.11 and 0.40.12):
  - gpt-oss's harmony format shows an object parameter as a bare `object`, without its properties. Removing `query_stats`'s `query`, 13,273 characters of schema, takes 25 tokens off its prompt.
  - gemma4's keeps an object's properties, `query`'s included, but dropped the `anyOf` of a period object's kinds.
  - Descriptions reach every model, these two included ("Why the tools and instructions are as they are" below).
- **Tool calls:** `deepseek-r1:8b`'s chat template in Ollama never shows the tool list, and `phi4-mini:3.8b` and often `olmo-3.1:32b-instruct` write tool calls as text that Ollama doesn't parse. `mistral-small3.2:24b` answers some questions from memory, with invented figures and links.

### What models get wrong
Counts are of the 27 answers each model gave to golden questions 1–9 at 3 epochs, in Ollama 0.40.11 or 0.40.12 at 64k, unless they come from the scored run.
- **No link:** answers in the model's own words, without the link an answer tool returned: `glm-4.7-flash` 12, `lfm2.5:24b` 8, `mistral-small3.2:24b` 8, `olmo-3.1:32b-instruct` 7, `nemotron-3.5-lightning:30b` 2 and `gpt-oss:20b` 1. Some name the label or the tool instead of giving the URL, such as `glm-4.7-flash`'s "Answer proof: Test player batting for 28114, 24 Jan 2009 to 24 Jan 2012". In the scored run, `qwen3:4b-instruct` left the link out of 20 of its 60 answers; in 14 of them the tool had answered, and the model restated the first line without its link.
- **Altered links**, such as a query link with fields cut off or its `;` swapped for `&`, or a link built from a profile ID: `lfm2.5:24b` 6, `gpt-oss:20b` 2 and `olmo-3.1:32b-instruct` 1.
- **No tool calls:** `olmo-3.1:32b-instruct` 12 and `mistral-small3.2:24b` 6, for the reasons in "Models" above. `glm-4.7-flash` once stopped without an answer after 19 calls.
- **Figures of their own:** answers add figures they worked out, such as differences, ratios, totals and rounded rates, which fail the grounding check (D13). In the scored run, all 6 of `qwen3.8:27b`'s misses failed only this check.
- **Named players:** most models compare named players, as golden question 6 asks, with one `player_record` call each, as the server instructions and `better_than_player`'s description say. `qwen3:4b-instruct` calls `better_than_player` for each instead, in every epoch of golden question 6 and its held-out case in the scored run: the tool's name matches the questions' "better". `glm-4.7-flash` (2 of 3 epochs) and `gpt-oss:20b` (1 of 3) also called only `better_than_player` for golden question 6.
- **Places:** in the one-epoch run in "Scores", `qwen3.8:27b` gave Asia, then three Asian countries, as `host_country` for golden question 6, instead of the continent.
- **Periods:** in the scored run, `gpt-oss:20b` guessed year ranges for golden question 4 and its held-out case, about a player's last 3 and 4 years, in 4 of 6 epochs; `qwen3:4b-instruct` and `qwen3.8:27b` gave "last 3 years" and "last 4 years" in all 6.
- **Minimums:** some models give `better_than_player` a minimum of 0, which makes the query too broad for D10; the error says to leave the minimum out.

### Why the tools and instructions are as they are
- **Periods are text (D42).** With `period` as an object of kinds, models went wrong where a renderer dropped the kinds ("Models" above): `gpt-oss:20b` invented fields, such as `{"kind": "dates", "from": …, "to": …}`, and `gemma4:31b` wrote every period as a string, such as "2000 to 2009" or "last 3 years". Models that saw the kinds also wrote strings: `lfm2.5:24b` "all_time" 9 times, and `nemotron-3.5-lightning:30b` and `olmo-3.1:32b-instruct` "career" once each. A string is a parameter every renderer shows.
- **Descriptions carry what a renderer may hide**, because they reach every model: `query_stats`'s `query` names `class` and `type` and lists their values, and `series_count`'s `number_of_teams` lists the values crickey reads (D44). With that `query` description, `gpt-oss:20b`, which sees none of `query`'s fields, gave a valid `class` and `type` in all 5 tries at listing the leading ODI wicket-takers against Australia with `query_stats`. All 5 queries failed on other fields, though, such as an opposition given by name rather than ID: models struggle to write `query_stats`'s queries even with its fields named.
- **Career spans:** `better_than_player`'s period description says to leave the period out, or give "career", for the span the player played, because crickey reads its dates from Statsguru. A model that can write the period otherwise guesses the years: `gpt-oss:20b` gave McCullum's T20I span as "2005 to 2013", "2006 to 2009" and "2004 to 2016" for golden question 3. With the description, over golden questions 1, 2, 3, 4 and 7 at 3 epochs, it gave "career" 11 times and its own span 3 times, and `gemma4:31b` and `qwen3.8:27b` never guessed one.
- **Link labels (D16):** models read a tool result's text, the same markdown as `answer_markdown`, where an answer's link is labelled "Answer proof", or "Input/method link" for a derived rate (D13). They never see the key's name, so the server instructions name the labels.
- **Full URLs (D16):** models retype links, and some point to a link instead of giving it. `qwen3.8:27b` once gave Hayden's profile as player 13395, where the tool said 5616 (golden question 1), and `nemotron-3.5-lightning:30b` wrote "【better_than_player proof link】" or "see the “Answer proof” link in the tool output". So the instructions say to cite only links that came from the tools, as full URLs copied exactly.
- **The first line** of each answer ends with its main link, the answer proof or else the input/method link, because small models often keep only that line, restated ("No link" above).
- **No fixed number of years:** small models copy a description's number of years instead of the question's. With "last 5 years" as the period example, `qwen3:4b-instruct` gave a question about a player's final four years "last 5 years" in all 3 epochs, and guessed year ranges such as "2013 to 2016" for one about the last 3 years. So the period descriptions show "first N years" and "last N years" and say that N is the number of years. With them, it gave golden question 4 "last 3 years" and its held-out case "last 4 years" in all 6 epochs of the scored run, and no call used the "first 5 years" of `player_record`'s example question.
- **Examples never quote an eval case (D45):**
  - Small models copy other values from examples too: `qwen3:4b-instruct` gave the opposition "Australia", an example's value, for questions that name no opposition. No call named an example's player.
  - So an example that shares a value with an eval case can make it pass by copying: golden question 4 asks about the last 3 years, and with "last 3 years" as the period example, `qwen3:4b-instruct` passed it in all 3 epochs.
  - Examples teach a kind of question: with a per-fifty rate but no per-hundred one among `leaderboard`'s examples, `gpt-oss:20b` sent golden question 1, about innings per hundred, to `query_stats` in 2 of 3 epochs. So the examples include "Who scored T20I hundreds most often, with at least 3 hundreds?".
  - Quoting the golden questions bought little: removing every quote from the descriptions lowered the passes of the scored run's three models by 3 of 90 golden answers and 4 of 90 held-out ones, within the variation between runs ("Scores" below).
- **Series (D44):** `series_count` names each series from the advanced form's series list (R4) and links only its proof, because models given each series' page as a link cited those pages instead. Over ten models, 4 of 30 answers to golden question 10 cited only series pages and left out the proof, and 2 made up what the series were.
- **Host country:** `series_count`'s description says that the country the series were played in goes in `host_country`, and a team that played in them in `team`. With examples that named golden question 10's host as a team, `lfm2.5:24b` put "played in Pakistan" in `team` in all 3 epochs; with that line, and another country as the example team, it put Pakistan in `host_country` in all 6 epochs, once in `team` as well.
- **Class numbers:** small models give a format as Statsguru's class number in text, such as "2" for ODIs, so crickey reads "2" as it reads 2. `qwen3:4b-instruct` wrote "2" in all 6 epochs of golden question 10 and its held-out case in the scored run, and `mistral-small3.2:24b` in 2 of 6 epochs of golden question 10.

### Scores
The scored run: all 20 cases, the 10 golden and the 10 held-out, for 3 epochs per model, with crickey 0.4.0 and Ollama 0.40.12 at a 64k context, on the Apple Silicon laptop above. Answers that passed every check, of 30 per model and set:

| Model | Golden | Held-out |
|---|---|---|
| `qwen3.8:27b` | 27 | 27 |
| `qwen3:4b-instruct` | 15 | 8 |
| `gpt-oss:20b` | 13 | 15 |
| All three, of 90 | 55 | 50 |

- 79 of the 90 golden answers and 73 of the 90 held-out ones cite the proof.
- One `qwen3:4b-instruct` reply ran away, and was cancelled and scored as it stood. The run sent 190 Statsguru requests, with no blocks.
- **Variation:** two runs of `qwen3:4b-instruct` with the same tools differed by 1 golden and 3 held-out answers, and two runs of `gpt-oss:20b` whose descriptions differed by one example, which could only help, by 5 held-out answers. So a difference of up to 5 in 30 is within the noise of 3 epochs.
- **One epoch:** `qwen3.8:27b` answered all 20 cases at 1 epoch in 18 minutes from an empty cache, with 68 Statsguru requests and no blocks; 9 of the 10 golden and 9 of the 10 held-out answers passed.

### Statsguru requests per question
In the eval logs of 27–31 Oct 2026, with every request 15 s apart, 11 models called crickey's tools in 1,861 questions:
- 72% of the questions sent no Statsguru request, because 78% of the pages they needed were already in the cache. Most of the rest sent 1 to 4.
- A tool call's median time grew about 15 s with each request it sent: 10 s for 1, 21 s for 2, 39 s for 3, 54 s for 4 and 73 s for 5.
