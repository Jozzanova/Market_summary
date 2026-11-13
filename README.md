# Market summary

A daily review of equity, bond, commodity and currency markets over the last 24 hours, a macro report on central banks,
a politics report and a calendar of upcoming events, in English and Norwegian, written each morning by a
scheduled cloud agent (playbook: `DAILY.md`).

Live: https://market-summary.jskjeltorp.workers.dev (public)

## Tabs

| Tab | What is on it |
|---|---|
| Reports | The markets, macro and politics reports by date, in English or Norwegian (toggle at the top); a snapshot table of key prices above the markets report; charts embedded by the reports |
| Alerts | Market-moving events, the last 20: large moves in the krone (I-44, EUR/NOK, USD/NOK), the main indices, the VIX, US yields, oil, gas and metals, flagged by a worker every five minutes against the previous close (plus shock keywords in the Reuters, Bloomberg, FT and WSJ feeds), and events added by an agent that reviews the news every two hours on weekdays (playbook ALERTS.md). The tab turns red, orange or green by the last 24 hours |
| News | Recent headlines with snippets from FT, WSJ, Bloomberg, CNBC, The Economist, The Guardian, E24, the ECB, the Federal Reserve and the Bank of England (public feeds) and Reuters, DN, Finansavisen and Norges Bank (through Google News), filterable by source and category; refreshed with the data |
| Upcoming events | Central bank meetings, data releases and political dates for the weeks ahead, by category |
| Equities | Indices, an AI and semiconductor group (SOX, NYSE FANG+, semiconductor, Magnificent 7 and AI ETFs) and US sectors with 1-day, 1-week, 1-month and year-to-date changes; indexed performance; sector bars |
| Rates & credit | US, euro-area and Norwegian yields and policy rates; credit spreads; 10-year and short-end charts; the US curve and real yields |
| Commodities | Energy, metals and grains with changes and indexed charts |
| Currencies | The dollar index and the main pairs including EUR/NOK and USD/NOK, and Norges Bank's import-weighted krone index I-44 |
| Central banks | Policy rates, last and next decisions; Norwegian policy and market rates; the next central bank events |
| Key figures | Growth, inflation, unemployment and rates for fourteen economies (the OECD, Eurostat, Statistics Norway, the BIS; the 12-month rate from each economy's own daily source where one exists), a ranked comparison and three-year history per indicator, and the krone matrix: NOK exchange rates with their changes and the policy, 3-month and 10-year rate differentials against each currency, plus the daily policy-rate differentials over the last year |
| Norges Bank | A daily report on everything said about Norges Bank (the bank, economists, the press, market pricing); policy rate, next decision, CPI and CPI-ATE against the 2 per cent target, the krone, Norwegian rates, the next Norges Bank dates |
| Update | Data status, how the page is made, sources |

Buttons at the top: **PDF** and **Word** download the selected day's reports in the current language as one formatted document (title, snapshot table, one report per page). The PDF is styled after Norges Bank's publications (midnight and accent blue, a sky band on the first page): each report opens with its heading and summary across the page, then runs in two columns with four small topical charts and the report's own figures set into the columns, rendered with Plotly at print resolution. pdfmake does not flow text between columns, so every block is measured in a dry layout pass and the columns are filled page by page; pdfmake is loaded on first use. Word is a .docx written in the page; **Excel** downloads every table as a workbook.

## Rebuild

```
python -X utf8 pipeline/fetch.py --refresh   # Yahoo (last month, spliced onto the previous history), FRED, ECB, Norges Bank -> data/data.json
python -X utf8 pipeline/fetch.py --refresh --full   # the full year of every instrument from Yahoo (done by itself on Sundays)
python -X utf8 pipeline/fetch.py --refresh --intraday   # prices only, in a few requests (the intraday routines); daily blocks as the morning run left them
python -X utf8 pipeline/fetch.py --offline   # the previous run's data (from the live site) as data.json, when every source is down
python -X utf8 pipeline/brief.py             # the numbers for the reports, as text

When Yahoo refuses the cloud address (HTTP 429, a shared egress address), `fetch.py` stops asking after four refusals and takes the prices from the reserve sources, CNBC's quote service and then TradingView (delayed up to 15 minutes), spliced onto the previous run's history; the brief and the data-quality list say so.
python -X utf8 build.py                      # inline data and reports into template.html -> site/index.html
npx wrangler deploy                          # Cloudflare Workers static assets, worker "market-summary"
```

Reports live in `data/letters/YYYY-MM-DD/<kind>.<lang>.md` (kind: markets, macro, politics, norgesbank; lang: en, no); intraday snapshots are `intraday-HHMM.<lang>.md` (Oslo time), all kept and shown newest first. The policy-rate
table is `data/policy_rates.csv`, the calendar `data/events.csv` the Norwegian CPI consensus `data/cpi_consensus.csv` and the latest decision statements and minutes `data/cb_minutes.json`; all are kept by the agent. The one-year policy-rate chart on the
Central banks tab is the BIS daily policy-rate dataset (WS_CBPOL, about a week behind) extended with the table after the latest decisions (the Fed as the upper bound of its target range). The Key figures tab reads the OECD SDMX API (consumer prices in both the COICOP 1999 and 2018 dataflows, labour force statistics, quarterly national accounts, financial indicators), Eurostat (euro-area unemployment) and Statistics Norway; the NOK crosses come from Yahoo Finance.
The table's market-pricing column is computed for the Fed from the CME 30-day Fed funds futures (Yahoo ZQ contracts, FedWatch arithmetic)
and kept by the agent from press reports for the other banks (columns p_hike, p_cut, pricing_note, pricing_date, pricing_url). The instrument universe
is `data/instruments.csv`.

The repository is `https://github.com/Jozzanova/market_summary`; the morning routine (playbook DAILY.md) runs every day at 06:05 Oslo time from the data and news available at 06:00; the intraday routines (playbook INTRADAY.md; "intraday update (Europe)" 09:05 and 12:05, "(US open)" 15:45, "(US close)" 22:15 Oslo time, crons in UTC) are all switched off since 13 November 2026 to concentrate on the morning report; their snapshots stay in `data/letters` and the Intraday view is hidden until they resume.
