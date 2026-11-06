# Market summary

A daily review of equity, bond, commodity and currency markets over the last 24 hours, a macro letter on central banks,
a politics letter and a calendar of upcoming events, in English and Norwegian, written each morning by a
scheduled cloud agent (playbook: `DAILY.md`).

Live: https://market-summary.jskjeltorp.workers.dev (public)

## Tabs

| Tab | What is on it |
|---|---|
| Letters | The markets, macro and politics letters by date, in English or Norwegian (toggle at the top); a snapshot table of key prices above the markets letter; charts embedded by the letters |
| Upcoming events | Central bank meetings, data releases and political dates for the weeks ahead, by category |
| Equities | Indices, an AI and semiconductor group (SOX, NYSE FANG+, semiconductor, Magnificent 7 and AI ETFs) and US sectors with 1-day, 1-week, 1-month and year-to-date changes; indexed performance; sector bars |
| Rates & credit | US, euro-area and Norwegian yields and policy rates; credit spreads; 10-year and short-end charts; the US curve and real yields |
| Commodities | Energy, metals and grains with changes and indexed charts |
| Currencies | The dollar index and the main pairs including EUR/NOK and USD/NOK, and Norges Bank's import-weighted krone index I-44 |
| Central banks | Policy rates, last and next decisions; Norwegian policy and market rates; the next central bank events |
| Norges Bank | A daily letter on everything said about Norges Bank (the bank, economists, the press, market pricing); policy rate, next decision, CPI and CPI-ATE against the 2 per cent target, the krone, Norwegian rates, the next Norges Bank dates |
| Update | Data status, how the page is made, sources |

Buttons at the top: **PDF** and **Word** download the selected day's four letters in the current language as one formatted document (title, snapshot table, one letter per page; PDF via pdfmake loaded on first use, Word as a .docx written in the page); **Excel** downloads every table as a workbook.

## Rebuild

```
python -X utf8 pipeline/fetch.py --refresh   # Yahoo, FRED, ECB, Norges Bank -> data/data.json (standard library only)
python -X utf8 pipeline/brief.py             # the numbers for the letters, as text
python -X utf8 build.py                      # inline data and letters into template.html -> site/index.html
npx wrangler deploy                          # Cloudflare Workers static assets, worker "market-summary"
```

Letters live in `data/letters/YYYY-MM-DD/<kind>.<lang>.md` (kind: markets, intraday, macro, politics, norgesbank; lang: en, no). The policy-rate
table is `data/policy_rates.csv` and the calendar `data/events.csv`; both are kept by the agent. The instrument universe
is `data/instruments.csv`.

The repository is `https://github.com/Jozzanova/market_summary`; the morning routine (playbook DAILY.md) runs every day at 06:05 Oslo time from the data and news available at 06:00; the intraday routine (playbook INTRADAY.md) runs on weekdays at 12:05 and 17:05 Oslo time and writes the intraday letter.
