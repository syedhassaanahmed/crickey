# Research notes

Everything learned while planning crickey, so it doesn't need to be redone. Unless marked otherwise, items were observed directly on 3 October 2026.
- **(search summary)** marks facts from a web-search summary rather than the primary page.
- **(unverified)** marks facts seen in other people's code that we haven't tested.

Pages fetched during research aren't committed to this repo.

## Contents
1. Access, robots.txt and terms
2. Statsguru URL model
3. Match classes and record types
4. Filter catalogue (advanced batting form)
5. Minimums and sort options by view (batting)
6. Player pages
7. Player search
8. Results pages
9. Records section (first-class and List A)
10. Example data snapshot
11. Existing MCP servers and tools
12. MCP protocol and Python SDK
13. Client configuration
14. Distribution options
15. Library versions and development environment
16. Open questions

## 1. Access, robots.txt and terms

### Hosts
- Statsguru is served from `stats.cricinfo.com`; `stats.espncricinfo.com` redirects there.
- Pages are server-rendered HTML. Result tables are `<table class="engineTable">` with data rows `<tr class="data1">`.
- Generic page readers that convert pages to markdown only see the cookie banner, so pages must be parsed as raw HTML.
- `www.cricinfo.com` and `www.espncricinfo.com` return 403 to scripts (bot protection), including their robots.txt and terms pages.

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

### Terms of use
- The Statsguru footer shows "© Jiostar India Pvt Ltd" and links to the Terms of Use (https://www.cricinfo.com/terms-of-use) and Privacy Policy (https://www.cricinfo.com/privacy-notice). The terms page returns 403 to scripts, so read it in a browser.
- **(search summary)** Cricinfo's Terms of Use, operated by JioStar and last updated 1 June 2026, say users may not "use data mining, robots, or similar data collection or extraction tools", or make unauthorised back-up or archival copies.
- Disney's Terms of Use (last updated 24 May 2024), which cover ESPN-branded products, ban extracting content "using a robot, spider, script, or other automated means", including for "creating or developing any AI Tool, data mining or web scraping". The Statsguru footer now points to JioStar's terms instead.

### User-Agent tests
All on the same results page: `https://stats.cricinfo.com/ci/engine/stats/index.html?class=1;orderby=runs;size=10;template=results;type=batting`

| User-Agent | Response |
|---|---|
| `Python-urllib/3.14` (Python's default) | 403 |
| `python-httpx/0.28.1` (httpx's default) | 403 |
| `curl/8.21.0` (curl's default) | 200 |
| `crickey/0.1` | 200 |

Earlier the same day, `Mozilla/5.0` and a full Chrome-like User-Agent also got 200 on form, results and player pages. Not yet tested: httpx sending curl's User-Agent string.

## 2. Statsguru URL model

| Page | URL |
|---|---|
| Filter form | `https://stats.cricinfo.com/ci/engine/stats/index.html?class=<class>;type=<type>` (add `filter=advanced` for the advanced form) |
| Results | The same parameters plus `template=results` |
| Player page | `https://stats.cricinfo.com/ci/engine/player/<id>.html?class=<class>;template=results;type=<type>[;view=<view>]` |
| Player search | `https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=<name>;template=analysis` |
| Records index | `https://stats.cricinfo.com/ci/engine/records/index.html?class=<class>` |

- **Separators:** parameters are separated by `;`, and `&` also works. Cricinfo redirects every query to a standard URL with parameters sorted alphabetically, for example `?class=2;orderby=hundreds;qualmin1=10;qualval1=hundreds;template=results;type=batting`.
- **Links inside pages:** player profiles are `/ci/content/player/<id>.html`; matches are `/ci/engine/match/<id>.html`.
- **Date range:** `spanmin1=12+Jun+2010;spanmax1=29+Jun+2024;spanval1=span`, with dates as `DD Mon YYYY`. The form's hidden `spanmin0` and `spanmax0` hold the class's first and latest match dates (Tests: 15 Mar 1877 to 09 Sep 2026 at the time).
- **Minimums:** `qualval1=<field>;qualmin1=<n>`, with optional `qualmax1`. The form shows only one, but `qualval2`/`qualmin2` and `qualval3`/`qualmin3` also work: a query with three minimums returned only rows meeting all three.
- **Sort:** `orderby=<field>`; `orderbyad=reverse` reverses the order.
- **Page size:** `size` is 10, 25, 50 (default), 100, 150 or 200. Results show "Page X of Y". The page-number parameter hasn't been confirmed.
- **Truncated decimals:** averages and strike rates are cut off, not rounded. Kohli's T20I average is 4188 ÷ 86 = 48.698, shown as 48.69.

## 3. Match classes and record types

| Class | Label | Where seen |
|---|---|---|
| 1 | Tests | Index tabs; player search ("Test matches") |
| 2 | ODIs | Index tabs |
| 3 | T20Is | Index tabs |
| 4 | First-class matches | Records index; the query engine returns 503 |
| 5 | List A matches | Records index; the query engine returns 503 |
| 6 | Twenty20 (all T20, including domestic and franchise leagues) | Index tabs; player search ("Twenty20 matches") |
| 8 | Women's Tests | Index "other" menu |
| 9 | Women's ODIs | Index tabs |
| 10 | Women's T20Is | Index tabs |
| 11 | All Test/ODI/T20I combined | Index tabs |
| 20 | Youth Tests | Index "other" menu |
| 21 | Youth ODIs | Index "other" menu |
| 22 | Youth T20Is | Index "other" menu |
| 23 | Women's T20 (all) | Index "other" menu |

- The records index also links classes 12, 13 and 16. One of its labels is "Combined First-class, List A and Twenty20", but which number it belongs to wasn't captured.
- Every class on the index page (1, 2, 3, 6, 8, 9, 10, 11, 20, 21, 22, 23) offers the same record types: `batting`, `bowling`, `fielding`, `allround`, `fow` (partnerships), `team`, `aggregate` and `official` (umpires and referees).
- The class 6 form lists domestic and franchise teams (for example Abahani Limited, Abbottabad Falcons, Adelaide Strikers, AJK Jaguars) as well as national teams. Their IDs haven't been captured yet.

## 4. Filter catalogue (advanced batting form)
Taken from the Tests form (`index.html?class=1;filter=advanced;type=batting`). Other classes have different values (more teams, other trophies), so each class needs its own lists.

**Teams (`team`, `opposition`):** 40 Afghanistan, 2 Australia, 25 Bangladesh, 1 England, 140 ICC World XI, 6 India, 29 Ireland, 5 New Zealand, 7 Pakistan, 3 South Africa, 8 Sri Lanka, 4 West Indies, 9 Zimbabwe. Player pages also list 32 Nepal, 15 Netherlands and 27 United Arab Emirates as oppositions.

**Host countries (`host`):** 2 Australia, 25 Bangladesh, 1 England, 6 India, 29 Ireland, 5 New Zealand, 7 Pakistan, 3 South Africa, 8 Sri Lanka, 27 United Arab Emirates, 4 West Indies, 9 Zimbabwe.

**Continents (`continent`):** 1 Africa, 3 Americas, 2 Asia, 4 Europe, 5 Oceania.

**Long lists:**
- `ground`: 127 values, from `131` (AUS: Adelaide Oval) to `261` (ZIM: Queens Sports Club, Bulawayo).
- `season`: 227 values, from `1876/77` to `2026`.
- `series`: 878 values, from `1` (England in Australia Test Series, 1876/77) to `17461` (Pakistan in England Test Series, 2026).

**Trophies (`trophy`, Tests):** 1105 Anderson-Tendulkar Trophy, 868 Anthony de Mello Trophy, 7 Asian Test Championship, 76 Basil D'Oliveira Trophy, 920 Benaud-Qadir Trophy, 6 Border-Gavaskar Trophy, 919 Botham-Richards Trophy, 10 Clive Lloyd Trophy, 1076 Crowe-Thorpe Trophy, 201 Freedom Trophy, 81 ICC Super Series Tests, 804 ICC World Test Championship, 143 MCC Spirit of Cricket Test Series, 94 Pataudi Trophy, 9 Sir Vivian Richards Trophy, 207 Sobers/Tissera Trophy, 8 Southern Cross Trophy, 1035 Tangiwai Shield, 1 The Ashes, 3 The Frank Worrell Trophy, 4 The Wisden Trophy, 5 Trans-Tasman Trophy, 2 Triangular Tournament, 99 Warne-Muralitharan Trophy.

**Choice filters.** Checkboxes accept several values; leaving a radio blank means "either".

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
| Dates | `spanmin1`, `spanmax1`, `spanval1=span` | The class's first match to today |
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

**Text inputs:** `search_player` ("match involving players") and `search_captain` ("match involving captains") look up a name and add it to the query. The URL parameters they produce haven't been captured.

## 5. Minimums and sort options by view (batting)
The advanced form contains one `qualval1` list and one `orderby` list per view (ids `havingselect_batting_<view>` and `orderbyselect_batting_<view>`), and shows the pair for the chosen view. It also includes lists for views that exist only on player pages (cumulative, partnerships, dismissals).

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
- **Batting:** matches = matches played; innings = innings batted; notouts = not outs; outs = batting dismissals; runs = runs scored; minutes = minutes batted; balls_faced = balls faced; batting_average; batting_strike_rate; hundreds = hundreds scored; fifty_plus = scores of fifty or more; ducks = ducks scored; fours, sixes = boundaries; high_score = highest innings score; batted_score = runs in the innings (innings view); batting_score1, batting_score2 = runs in the 1st and 2nd innings of a match.
- **Context:** player = player name; start = start date; age = age at the start of the match; batting_position = batting order position; dismissal = method of dismissal; innings_number = innings number in the match; year = year of match start; season = match season.
- **Partnerships (`fow_*`):** fow_wicket = fall-of-wicket number; fow_score = partnership runs; fow_in, fow_out = team score at the partnership's start and end; fow_innings = number of partnerships; fow_notouts = unbroken partnerships; fow_outs = broken partnerships; fow_runs = total partnership runs; fow_high_score = highest partnership; fow_average = average partnership per dismissal; fow_hundreds = century partnerships; fow_fifty_plus = partnerships of fifty or more; partner = partner name.
- **Dismissals (`dis_*`):** dis_matches = matches against each other; dis_dismissals = total dismissals; dis_bowled, dis_caught_fielder, dis_caught_keeper, dis_stumped, dis_lbw, dis_hit_wicket, dis_run_out, dis_other, dis_not_out = counts by type; dis_average = average score upon dismissal; dis_ducks = ducks; dis_matches_per_dismissal = matches per dismissal; dis_runs = batter's runs in the innings; dis_innings_number = innings number in the match; dis_how_out = method of dismissal; dis_bowler, dis_fielder = bowler or fielder who took the dismissal; dis_span = playing span against each other.

The equivalent lists for the bowling, fielding, all-round, partnership, team and aggregate forms haven't been captured.

## 6. Player pages
- **URL:** `https://stats.cricinfo.com/ci/engine/player/<id>.html?class=<class>;template=results;type=<type>[;view=<view>]`
- **Types (`type`):** `allround`, `batting`, `bowling`, `fielding`.
- **Views (`view`):** the form offers 24 options. The grouping below is inferred from the form's layout.
  - Batting: blank for career summary, `innings`, `match`, `cumulative`, `reverse_cumulative`, `series`, `ground`, `results` (match results), `awards_match`, `awards_series`, `fow_summary` (partnership summary), `fow_list`, `dismissal_summary`, `bowler_summary`, `fielder_summary`, `dismissal_list`.
  - Bowling: `dismissal_summary` (wickets summary), `batsman_summary` (batters dismissed), `fielder_summary`, `dismissal_list` (list of wickets).
  - Fielding: `dismissal_summary`, `batsman_summary`, `bowler_summary`, `dismissal_list`.
- **Filters:** `opposition`, `host` and `ground` (lists specific to the player; Kohli's ODIs have 14 oppositions, 10 hosts and 73 grounds), `home_or_away`, dates (`spanmin1` and `spanmax1`, defaulting to the player's first match and today), `spanquickpick`, `season` and `result` (1 won, 2 lost, 3 tied, 5 no result).
- **Format tabs** show each format's span. For Kohli: "Tests (2011 - 2024/25)", "ODIs (2008 - 2026/27)" and "T20Is (2010 - 2024)", plus an "other" menu with class 11 (All Test/ODI/T20I), 6 (Twenty20), 20 (Youth Tests) and 21 (Youth ODIs).
- **Career summary columns (T20I batting):** Span, Mat, Inns, NO, Runs, HS, Ave, BF, SR, 100, 50, 0, 4s, 6s. The summary page also has tables split by a "Grouping" column (for example one row per opposition: "v Afghanistan", "v Australia").
- **Innings list columns:** Runs, Mins, BF, 4s, 6s, SR, Pos, Dismissal, Inns, (blank), Opposition, Ground, Start Date.

## 7. Player search
- `https://stats.cricinfo.com/ci/engine/stats/analysis.html?search=kohli;template=analysis` returned 9 people.
- Each row has a short name, full name and country code, then one entry per format with its span and match count. Each entry links to `/ci/engine/player/<id>.html?class=<class>;type=allround`.
- Example row: "V Kohli (Virat Kohli) IND", with "Test matches player (2011 - 2024/25, 123 matches)", "One-Day Internationals player (2008 - 2026/27, 317 matches)", "Twenty20 Internationals player (2010 - 2024, 125 matches)", "Combined Test, ODI and T20I player (2008 - 2026/27)" and "Under-19s Youth Test matches player (2006 - 2007/08, …)".
- Results mix men, women, youth players and officials with the same surname (for example A Kohli, Germany, women's T20Is; PS Kohli, India, Twenty20; entries ending "official"). Name lookups must filter by class, and ideally by country.

## 8. Results pages
- **Batting columns (overall view):**
  - ODIs and T20Is: Player, Span, Mat, Inns, NO, Runs, HS, Ave, BF, SR, 100, 50, 0
  - Tests: Player, Span, Mat, Inns, NO, Runs, HS, Ave, 100, 50, 0 (no balls faced or strike rate)
- **Player cell:** "Name (COUNTRY)", for example "V Kohli (IND)". T20I lists include players from associate nations (for example QAT and JPN).
- **Sort caption:** for example "Ordered by runs scored (descending)".
- **Freshness note:** each results page says "Statsguru includes the following current or recent <format> matches:", followed by match names, dates and links (`/ci/engine/match/<id>.html`, labelled like "Test # 2635").

## 9. Records section (first-class and List A)
- Statsguru's query engine returned 503 for `class=4` and `class=5` (two requests 16 s apart), while `class=6` returned 200 at the same time.
- `https://stats.cricinfo.com/ci/engine/records/index.html` returned 200 and links classes 1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 16, 20, 21 and 23. Labels include "First-class matches", "List A matches", "Twenty20 matches" and "Combined First-class, List A and Twenty20".
- `records/index.html?class=4` (title "Records | First-class matches | Cricinfo.com") returned 200. It links fixed record lists at `/ci/content/records/<id>.html` and team records at `/ci/engine/records/index.html?category=2;class=4`.
- Fetching `/ci/content/records/251072.html` returned 503, so these pages may not be reachable. The List A index wasn't fetched.

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

The Statsguru header links league record pages on www.cricinfo.com (blocked to scripts) with numeric IDs in the path: IPL 117, BBL 158, PSL 205, CPL 748, SA20 987, ILT20 946, LPL 865, MLC 985, BPL 159, the Blast (`twenty20-cup-england`) 113, The Hundred (men's) 826, men's T20 World Cup 89, World Cup 12, Champions Trophy 44 and Under-19 World Cup 109. Women's: T20 World Cup 136, World Cup 68, WBBL 720, WPL 988 and The Hundred 834. **(unverified)** These may be the same IDs as Statsguru's `trophy` filter values.

## 10. Example data snapshot
Useful for tests and as known answers.

**ODI batters with at least 10 hundreds:** 64 players on one page.
`https://stats.cricinfo.com/ci/engine/stats/index.html?class=2;orderby=hundreds;qualmin1=10;qualval1=hundreds;size=200;template=results;type=batting`

| Player | Span | Mat | Inns | NO | Runs | HS | Ave | BF | SR | 100 | 50 | 0 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| V Kohli (IND) | 2008-2026 | 317 | 304 | 48 | 15109 | 183 | 59.01 | 16010 | 94.37 | 55 | 79 | 18 |
| SR Tendulkar (IND) | 1989-2012 | 463 | 452 | 41 | 18426 | 200* | 44.83 | 21368 | 86.23 | 49 | 96 | 20 |
| RG Sharma (IND) | 2007-2026 | 291 | 282 | 37 | 12028 | 264 | 49.09 | 12894 | 93.28 | 35 | 62 | 16 |

**Virat Kohli (ID 253802), T20I batting career:** 2010–2024, 125 matches, 117 innings, 31 not outs, 4188 runs, highest 122*, average 48.69, 3056 balls, strike rate 137.04, 1 hundred, 38 fifties, 7 ducks, 369 fours and 124 sixes. His innings list has 125 rows, from 12 Jun 2010 to 29 Jun 2024.
- Career: `https://stats.cricinfo.com/ci/engine/player/253802.html?class=3;template=results;type=batting`
- Innings: the same URL plus `;view=innings`

**T20I batters with at least 1,000 runs between 12 Jun 2010 and 29 Jun 2024:** 149 players.
`https://stats.cricinfo.com/ci/engine/stats/index.html?class=3;orderby=batting_average;qualmin1=1000;qualval1=runs;size=200;spanmax1=29+Jun+2024;spanmin1=12+Jun+2010;spanval1=span;template=results;type=batting`

| Player | Runs | Ave | SR |
|---|---|---|---|
| Mohammad Rizwan (PAK) | 3313 | 48.72 | 126.45 |
| V Kohli (IND) | 4188 | 48.69 | 137.04 |
| JP Duminy (SA) | 1478 | 47.67 | 127.52 |
| Muhammad Tanveer (QAT) | 1499 | 45.42 | 138.02 |
| MS Dhoni (IND) | 1176 | 45.23 | 132.28 |
| K Kadowaki-Fleming (JPN) | 1089 | 43.56 | 156.91 |
| SA Yadav (IND) | 2340 | 43.33 | 167.74 |
| Babar Azam (PAK) | 4145 | 41.03 | 129.08 |

**The same query, also requiring average ≥ 48.69 and strike rate ≥ 137.04,** returned 1 row (Kohli himself), so nobody beat him on both:
`https://stats.cricinfo.com/ci/engine/stats/index.html?class=3;orderby=batting_average;qualmin1=1000;qualmin2=48.69;qualmin3=137.04;qualval1=runs;qualval2=batting_average;qualval3=batting_strike_rate;size=200;spanmax1=29+Jun+2024;spanmin1=12+Jun+2010;spanval1=span;template=results;type=batting`

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
  - `json_response=True` returns plain JSON and drops progress notifications, so keep the default (SSE) to send progress.
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
Researched before choosing Docker-only sharing.

| Option | How users install | Notes |
|---|---|---|
| Package runners | `uvx` (PyPI), `npx` (npm) or `dnx` (NuGet) | Needs the runtime and a published package. |
| `uvx` from GitHub | A release wheel URL or `git+https://…` | Needs uv, plus git for git URLs. |
| Docker image | `docker run -i --rm …` | Needs Docker. |
| MCP Bundle (`.mcpb`) | Download and double-click | A zip with a `manifest.json`, built with `mcpb init` and `mcpb pack` (`npm install -g @anthropic-ai/mcpb`). Manifest 0.4 adds `server.type: "uv"`, where the host app manages Python and dependencies (plain Python bundles can't ship compiled packages such as pydantic). `user_config` fields appear as an install form. Mainly for Claude Desktop; the docs recommend Node.js for the fewest dependencies. |
| Copilot plugins | `copilot plugin install`, `/plugin install` or `enabledPlugins` | Agent Plugins 1.0: `plugin.json` with `$schema` `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`, `mcp.json` and `skills/` at the root, and Copilot-only parts under `com.github.copilot/`. |
| One-click install links | Buttons in a README | VS Code and Cursor. |
| Hosted remote server | Add a URL | Works with web chat apps, but means public hosting (ruled out). |

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

## 15. Library versions and development environment
Latest versions on PyPI, 3 Oct 2026:

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
| hishel | 1.4.0 | 2026-09-16 | |
| respx | 0.23.1 | 2026-04-08 | |
| pytest | 9.1.1 | 2026-06-19 | |

Development machine: Windows with Python 3.14.6, uv 0.11.21, Node.js 24.21.0, Docker 29.5.2, git 2.55.0, gh 2.101.0 and curl 8.21.0.

## 16. Open questions
To check while building:
- Does httpx get 200 when it sends curl's User-Agent string?
- Which parameter selects results pages after the first?
- Which URL parameters do "match involving players" and "match involving captains" produce?
- How many decimal places does `qualmin` accept? (Needed for strict comparisons.)
- What are the minimum and sort options for the bowling, fielding, all-round, partnership, team and aggregate forms?
- What are the team IDs for class 6 (domestic and franchise teams) and the other classes?
- Are the league IDs on www.cricinfo.com the same as Statsguru's `trophy` values?
- Do date ranges work in player-page URLs? (The form has them.)
- Are the 503 responses for first-class and List A permanent, and can `/ci/content/records/<id>.html` pages be fetched?
- Which class numbers are "Combined First-class, List A and Twenty20" and classes 13 and 16?
