# Daily market summary routine

This file is the playbook for the scheduled cloud agent that writes the market summary every morning. You start
with a fresh clone of this repository and no other context; read this file first, then do the steps in order. Python's
standard library is enough (no packages to install); Node is used for the deploy. Do not end the run before step 6 is
done and the letters are committed.

## 1. Refresh the data

    git pull --rebase origin main
    python -X utf8 pipeline/fetch.py --refresh     # 1-3 minutes; give it a 10-minute timeout
    python -X utf8 pipeline/alerts.py              # data alerts (large moves) into data/alerts.json, the Alerts tab
    python -X utf8 pipeline/brief.py               # the numbers for the letters

`fetch.py` downloads one year of daily prices for every instrument in `data/instruments.csv` from Yahoo Finance, the
US yields and credit spreads from FRED, the euro-area AAA curve from the ECB, the Norwegian rates from Norges Bank, the
policy rates from the BIS and the key figures of thirteen economies from the OECD and Eurostat (block `macro`), and
writes `data/data.json`. A source that fails keeps the previous run's block and is listed under `kept_from_previous`;
mention it in the report. `403 Forbidden` from the proxy means the cloud environment's network allowlist lacks that host.

`brief.py` prints every table with one-day, one-week, one-month and year-to-date changes, the largest moves, the curve,
the policy rates on file and the upcoming events on file. Use its numbers in the letters; do not type numbers from memory.

## 2. Read the news

`brief.py` ends with the latest headlines from the outlets' feeds (FT, WSJ, Bloomberg, Reuters, CNBC, E24, DN, Finansavisen, the central banks) with links: start there, open the articles you use, and search the web for what the feeds miss.

Search the web for what moved markets in the last 24 hours and why: US close, European close, the Asian session this
morning, government bonds, credit, oil, gas, metals, FX; central bank decisions, speeches, minutes and reports; the macro
data released yesterday and due today; and the political developments that matter for markets (elections, fiscal policy,
trade, sanctions, geopolitics). Always cover Norway: Oslo Børs, the krone, what others expect of Norges Bank, Statistics Norway releases.
**Register.** Write as a central bank writes: measured, precise, impersonal, without journalistic colour. Separate what
happened from what it means, and give the reasoning behind every interpretation (the mechanism: oil and the terms of
trade for the krone, rates and the differential, risk appetite, base effects in the inflation numbers). State uncertainty
plainly ("trolig", "tyder på", "no source attributes the move") and never dress a guess as a fact. Prefer "prisveksten",
"styringsrenten", "kronekursen", "rentedifferansen", "markedet venter", "innstramming", "framover"; avoid "rally", "frykt",
"sjokk", "kalddusj", "haukete", "prøvestein" and headlines' superlatives. Say what a number implies for the inflation
outlook, the rate path and the krone before moving on; one careful sentence of interpretation is worth more than three
adjectives. The same in English: "inflation", "the policy rate", "the krone", "the differential", "risk appetite",
"tightening"; "per cent", "basis points", "percentage points" written out.

**Source quality is part of the job.** Use the best source available for every fact, in this order:

1. Primary: central banks (norges-bank.no, federalreserve.gov, ecb.europa.eu, bankofengland.co.uk, boj.or.jp, riksbank.se,
   rba.gov.au, rbnz.govt.nz), statistics agencies (ssb.no, bls.gov, bea.gov, ec.europa.eu/eurostat, ons.gov.uk), finance
   ministries and governments (treasury.gov, whitehouse.gov, regjeringen.no), exchanges and index providers, company
   releases (the company's own site, SEC EDGAR, newsweb.oslobors.no), IEA, OPEC, the IMF and the BIS.
2. Established financial press: Financial Times, Bloomberg, Reuters, Wall Street Journal, and for Norway E24, DN,
   Finansavisen and NRK. CNBC, Nikkei Asia, Handelsblatt, Les Echos and the Economist are fine as well.
3. Everything else (FXStreet, Kitco, StockTitan, Trading Economics, Asia Times, aggregators and brokers' blogs) only
   when a fact exists nowhere in tiers 1 and 2, and never as the sole source for a number, a quote or a market move.
   When a tier-3 page cites a tier-1 or tier-2 source, go to that source and cite it instead.

Do not use investingLive (investinglive.com) at all, not even as a tier-3 fallback: everything it covers is available
from Reuters, Bloomberg, FT or the primary source.

Open the articles you cite. Every fact you use must have a source you can link; do not state anything you could not
verify, and say so when something could not be confirmed (for example "no report of the Asian session was available").
Before you build, count the citations per domain across the day's letters: if tier-3 sources are more than one in six,
go back and replace them.

## 3. Keep the tables current

- `data/policy_rates.csv`: one row per central bank (Fed, ECB, BoE, BoJ, Norges Bank, Riksbank, SNB, RBA, BoC). After any
  decision, update `rate`, `last_decision`, `last_move` (hold, cut or hike), `next_meeting` and `remaining_2026`; keep
  `source_url` pointing at the bank's own page. The one-year policy-rate chart is the BIS daily series extended with this
  table, so a wrong `rate` or `last_decision` shows up as a wrong step in the figure.
  Market pricing columns, every morning for every bank except the Federal Reserve (whose pricing is computed from the
  Fed funds futures): `p_hike` and `p_cut` are the probabilities in per cent (integers, blank if unknown) of a hike or a
  cut at the next meeting as reported that morning or the day before by Reuters, Bloomberg, FT, DN, E24, Finansavisen or
  a bank's market comment ("markets price a 70 % chance of a December hike"; "fully priced" = 95, "no change expected"
  = 0/0); `pricing_note_en`/`pricing_note_no` one line quoting the figure as reported, `pricing_date` (YYYY-MM-DD) and
  `pricing_url`. Update the columns when a newer report is found; leave them as they are otherwise.
- `data/cb_minutes.json`: for each of the nine banks the most recent decision statement and the most recent published
  minutes (or the bank's equivalent: ECB account, BoJ summary of opinions, BoC summary of deliberations; Norges Bank and
  the SNB have no minutes, so their monetary policy assessment stands in). Fields per item: bank, kind (decision|minutes),
  title, date (YYYY-MM-DD), url (the bank's own page), summary_en and summary_no (3–5 sentences, central-bank register:
  the decision and vote, the reasoning, the guidance, notable dissent), quotes (one or two verbatim lines under 25 words).
  After every decision replace that bank's decision item; when minutes are published replace its minutes item; set
  `updated` to today's date whenever you check. Read the documents on the bank's site; never summarise from memory.
- `data/cpi_consensus.csv`: the consensus for the next Norwegian CPI release, one row per month (month YYYY-MM, release_date,
  kpi, kpiate as 12-month rates in per cent, source such as "Bloomberg consensus (E24)", source_url). Add the row as soon as
  a survey median is reported (E24, DN, Finansavisen, TDN Direkt, Bloomberg); keep the rows after the release so the surprise
  stays visible as a dot against the printed line on the Norges Bank tab.
- `data/events.csv`: the calendar of upcoming events for the next four to six weeks. Columns: date (YYYY-MM-DD), time_oslo
  (HH:MM or blank), region (US, EA, UK, JP, NO, SE, CH, AU, CA, CN, global), category (central bank, data, politics,
  markets, other), event, importance (high, medium, low), note_en, note_no (the consensus or what to look for), source_url.
  Add what you learn (central bank meetings, CPI, payrolls, GDP, PMIs, Norwegian CPI and Norges Bank dates, elections,
  summits, fiscal deadlines), keep the dates verified, and delete rows older than yesterday.

## 4. Write the five letters, in English and in Norwegian

(The intraday snapshots, `intraday-HHMM.en.md` / `intraday-HHMM.no.md`, are written by the separate intraday routine, playbook `INTRADAY.md`; leave any existing intraday files alone.)

Create the folder `data/letters/YYYY-MM-DD/` (today's date, UTC) with ten files: `highlights.en.md`, `highlights.no.md`,
`markets.en.md`, `markets.no.md`, `macro.en.md`, `macro.no.md`, `politics.en.md`, `politics.no.md`, `norgesbank.en.md`,
`norgesbank.no.md`. Write the key points last, from the other four, so they agree. The Norwegian versions carry the same facts in
Norwegian bokmål (not a word-for-word translation; Norwegian number style: comma decimals, space thousands, "prosent").
Format: Markdown with a `# Title, weekday day month year` heading, where the title is just the name of the piece, never
"letter" or "brev": `Key points` / `Hovedtrekk`, `Markets` / `Markedet`, `Macro` / `Makro`, `Politics` / `Politikk`,
`Norges Bank` / `Norges Bank` (for example `# Markedet, tirsdag 10. november 2026`); `##` sections, bullets, bold lead-ins, Markdown tables
where a table says it better, and links in the text `([Source](url))`. A line `{chart: SYM1,SYM2 | 3m | indexed}` draws a
chart of the named instruments (symbols as in `data/instruments.csv`, or the FRED/ECB/Norges Bank keys `DGS2`, `DGS10`,
`EA_SR_10Y`, `nok_10y`, `nok_policy`, `BAMLH0A0HYM2`, and the derived rate differentials `diff_nok_eur_short` (Norway 3-year
minus euro AAA 2-year), `diff_nok_eur_10y` and `diff_nok_eur_policy`); range `1m`, `3m`, `ytd` or `1y`; mode `indexed`
(start = 100), `level`, or `dual` (the first series on the left axis, the rest on a right axis in percentage points: the
mode for an exchange rate against rate differentials). Use one or two charts per letter where they help. The krone
section of the Norges Bank letter uses `{chart: EURNOK=X,diff_nok_eur_short,diff_nok_eur_10y | 3m | dual}`, never
EUR/NOK and a yield on one axis.

Tone: measured, factual, central-bank style. State what happened, how much, and the likely reason, in that order; no
exclamation, no advice, no speculation presented as fact; "per cent" and "basis points" written out. Length: key points
200-320 words, markets 400-600 words, macro 400-600 words, politics 250-450 words.

- **Key points** (`highlights`, Norwegian title `Hovedtrekk`; the first and default letter on the page, its button is red): three
  short paragraphs with bold lead-ins and nothing else, no chart. (1) **Markets** / **Markedene**: how the broad equity
  indices (US, Europe, Norway, Asia), government yields (US, euro-area AAA, Norway 10-year) and the exchange rates (dollar
  index, EUR/USD, USD/JPY, Brent as the price that moves the krone) moved, with the numbers. (2) **Drivers** / **Drivkrefter**:
  the likely factors behind the moves, taken from what the markets were about in the last 24 hours: statements from the US
  president or US authorities, central bank decisions and speeches (Fed, ECB, Bank of England, Bank of Japan, the
  Riksbank, the RBA, the RBNZ), key figures, the oil price, geopolitics, company news. (3) **Norway** / **Norge**: the krone
  (EUR/NOK, USD/NOK, I-44 and why it moved), the rate differential against the euro area (policy rate, and 10-year against
  the AAA curve), what others expect Norges Bank to do (the day's named views and market pricing) and the Norwegian data
  of the day against consensus. The reader works at Norges Bank: mention the bank's own communication only when it is
  new, in half a sentence, and never retell it. Short and concise: say what happened,
  how much and the likely reason; every number from `brief.py` or the other letters of the day.
- **Markets** / **Markedet** (`markets`): a bold summary paragraph; sections Equities (US, Europe, Norway, Asia; say how the AI and semiconductor proxies in the brief, SOX, NYSE FANG+, the Magnificent 7 and AI ETFs, did against the market), Fixed income and
  credit, Commodities and currencies (always the krone: EUR/NOK, USD/NOK and Norges Bank's import-weighted index I-44, where
  a rise is a weaker krone; the brief prints all three), Today (the calendar for the day with consensus where known). The page shows a table
  of key prices above the letter automatically, so the text should explain rather than list.
- **Macro** / **Makro** (`macro`): central banks and the business cycle. A bold summary; a table of this week's or the latest
  decisions when there were any; a section per central bank that acted or spoke; the data of note; an Assessment section
  on the balance of risks.
- **Politics** / **Politikk** (`politics`): the political developments that matter for markets, by region (United States, Europe,
  Middle East and energy, Asia, Norway, as relevant), each with the market channel; a Dates to watch section.
- **Norges Bank** (`norgesbank`, shown on the Norges Bank tab): what others said about the Norwegian economy in
  the last 24 hours, what they expect Norges Bank to do, and the new Norwegian data. The reader works at Norges Bank, so
  do not retell what the bank itself did or said: its own communication gets one line, and only when it is new (a
  decision, a speech, a report); check norges-bank.no for that line and move on. Sources to check every day: the
  Norwegian press (E24, DN, Finansavisen, NRK, Nettavisen, NTB as carried by frifagbevegelse.no and TV 2), bank
  economists' comments (DNB Carnegie, Nordea, Handelsbanken, SEB, Danske, Swedbank, SpareBank 1 Markets, SpareBank 1
  Sør-Norge), international coverage (Bloomberg, Reuters, FXStreet notes from Nomura, Commerzbank and others), market
  pricing of the rate path (the probability of a move at the next meeting as quoted by economists or the press, FRA and
  NOWA pricing), and the data of the day (SSB: CPI, unemployment, GDP, retail trade, wages; NAV unemployment; Eiendom
  Norge house prices; the PMI; Norges Bank's Regional Network and Expectations Survey when published). Structure: a
  bold summary; New data / Nye nøkkeltall (what came out, against consensus and against Norges Bank's own forecast, and
  what drove it); What others say / Hva andre sier (named economists and strategists with employer, a short quote and a
  link, on the economy and on the rate path: hike, hold, peak reached, cuts; say plainly when none of the economists quoted
  expects a move); The krone and rates / Kronen og rentene (EUR/NOK, USD/NOK and the I-44 index, all in the brief, the
  differentials against the euro area, and the dual chart); Dates (next decision, CPI, Regional Network, MPR, the
  Financial Stability Report). On a day with no new comments or data, say so in one line and summarise the standing
  expectations and the next dates. 250-450 words. The tab shows the policy rate, next decision, CPI and CPI-ATE, the
  10-year yield and EUR/NOK automatically, so the text should interpret rather than list.

## 5. Build and deploy

    python -X utf8 build.py
    npx wrangler deploy                         # uses CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID from the environment; never write them into the repository

`npx wrangler deploy` must print `https://market-summary.jskjeltorp.workers.dev`.

## 6. Commit and push

    git add -A
    git commit -m "Market summary YYYY-MM-DD"
    git pull --rebase origin main && git push origin main

`data/data.json` is versioned (so the page can be rebuilt from the repository); `rawData/` and `site/` are not.

## 7. Report

End with a short report: the three summary paragraphs (English), the citation count per domain for the day's letters with
the share from tier-3 sources, what changed in the policy-rate table and the calendar,
sources that were kept from the previous run, the deployed address and the commit.
