# Daily market summary routine

This file is the playbook for the scheduled cloud agent that writes the market summary every morning. You start
with a fresh clone of this repository and no other context; read this file first, then do the steps in order. Python's
standard library is enough (no packages to install); Node is used for the deploy. Do not end the run before step 6 is
done and the letters are committed.

## 1. Refresh the data

    git pull --rebase origin main
    python -X utf8 pipeline/fetch.py --refresh     # 1-3 minutes; give it a 10-minute timeout
    python -X utf8 pipeline/brief.py               # the numbers for the letters

`fetch.py` downloads one year of daily prices for every instrument in `data/instruments.csv` from Yahoo Finance, the
US yields and credit spreads from FRED, the euro-area AAA curve from the ECB and the Norwegian rates from Norges Bank, and
writes `data/data.json`. A source that fails keeps the previous run's block and is listed under `kept_from_previous`;
mention it in the report. `403 Forbidden` from the proxy means the cloud environment's network allowlist lacks that host.

`brief.py` prints every table with one-day, one-week, one-month and year-to-date changes, the largest moves, the curve,
the policy rates on file and the upcoming events on file. Use its numbers in the letters; do not type numbers from memory.

## 2. Read the news

Search the web for what moved markets in the last 24 hours and why: US close, European close, the Asian session this
morning, government bonds, credit, oil, gas, metals, FX; central bank decisions, speeches, minutes and reports; the macro
data released yesterday and due today; and the political developments that matter for markets (elections, fiscal policy,
trade, sanctions, geopolitics). Always cover Norway: Oslo Børs, the krone, Norges Bank, Statistics Norway releases.
Prefer primary sources (central banks, statistics agencies, exchanges) and established outlets (Reuters, Bloomberg, FT,
WSJ, CNBC, E24, DN, Finansavisen). Every fact you use must have a source you can link; do not state anything you could
not verify, and say so when something could not be confirmed (for example "no report of the Asian session was available").

## 3. Keep the tables current

- `data/policy_rates.csv`: one row per central bank (Fed, ECB, BoE, BoJ, Norges Bank, Riksbank, SNB, RBA, BoC). After any
  decision, update `rate`, `last_decision`, `last_move` (hold, cut or hike), `next_meeting` and `remaining_2026`; keep
  `source_url` pointing at the bank's own page.
- `data/events.csv`: the calendar of upcoming events for the next four to six weeks. Columns: date (YYYY-MM-DD), time_oslo
  (HH:MM or blank), region (US, EA, UK, JP, NO, SE, CH, AU, CA, CN, global), category (central bank, data, politics,
  markets, other), event, importance (high, medium, low), note_en, note_no (the consensus or what to look for), source_url.
  Add what you learn (central bank meetings, CPI, payrolls, GDP, PMIs, Norwegian CPI and Norges Bank dates, elections,
  summits, fiscal deadlines), keep the dates verified, and delete rows older than yesterday.

## 4. Write the four letters, in English and in Norwegian

Create the folder `data/letters/YYYY-MM-DD/` (today's date, UTC) with eight files: `markets.en.md`, `markets.no.md`,
`macro.en.md`, `macro.no.md`, `politics.en.md`, `politics.no.md`, `norgesbank.en.md`, `norgesbank.no.md`. The Norwegian versions carry the same facts in
Norwegian bokmål (not a word-for-word translation; Norwegian number style: comma decimals, space thousands, "prosent").
Format: Markdown with a `# Title, weekday day month year` heading, `##` sections, bullets, bold lead-ins, Markdown tables
where a table says it better, and links in the text `([Source](url))`. A line `{chart: SYM1,SYM2 | 3m | indexed}` draws a
chart of the named instruments (symbols as in `data/instruments.csv`, or the FRED/ECB/Norges Bank keys `DGS2`, `DGS10`,
`EA_SR_10Y`, `nok_10y`, `nok_policy`, `BAMLH0A0HYM2`); range `1m`, `3m`, `ytd` or `1y`; mode `indexed` (start = 100) or
`level`. Use one or two charts per letter where they help.

Tone: measured, factual, central-bank style. State what happened, how much, and the likely reason, in that order; no
exclamation, no advice, no speculation presented as fact; "per cent" and "basis points" written out. Length: markets
400-600 words, macro 400-600 words, politics 250-450 words.

- **Markets letter** (`markets`): a bold summary paragraph; sections Equities (US, Europe, Norway, Asia), Fixed income and
  credit, Commodities and currencies, Today (the calendar for the day with consensus where known). The page shows a table
  of key prices above the letter automatically, so the text should explain rather than list.
- **Macro letter** (`macro`): central banks and the business cycle. A bold summary; a table of this week's or the latest
  decisions when there were any; a section per central bank that acted or spoke; the data of note; an Assessment section
  on the balance of risks.
- **Politics letter** (`politics`): the political developments that matter for markets, by region (United States, Europe,
  Middle East and energy, Asia, Norway, as relevant), each with the market channel; a Dates to watch section.
- **Norges Bank letter** (`norgesbank`, shown on the Norges Bank tab): everything said about Norges Bank in the last 24
  hours and what it means. Sources to check every day: norges-bank.no (press releases, the policy rate page, speeches,
  the Monetary Policy Report, the Regional Network, the Financial Stability Report, minutes and the oil fund NBIM when it
  is major news), the Norwegian press (E24, DN, Finansavisen, NRK), bank economists' comments (DNB Carnegie, Nordea,
  Handelsbanken, SEB, Danske, Swedbank), international coverage (Bloomberg, Reuters, FXStreet notes from Nomura,
  Commerzbank and others), market pricing of the rate path and the krone. Structure: a bold summary; What Norges Bank
  said (quote the statement where possible); What economists and the market say (named, with links); The krone and
  rates; Dates (next decision, CPI, Regional Network, MPR). On a day with no Norges Bank news, say so in one line and
  summarise the standing view and the next dates. 250-450 words. The tab shows the policy rate, next decision, CPI and
  CPI-ATE, the 10-year yield and EUR/NOK automatically, so the text should interpret rather than list.

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

End with a short report: the three summary paragraphs (English), what changed in the policy-rate table and the calendar,
sources that were kept from the previous run, the deployed address and the commit.
