# Intraday update routine

This file is the playbook for the scheduled cloud agents that write the intraday reports on weekdays. There are four runs
a day, Oslo time: about 09:05 (after the European open), 12:05 (noon), 15:40 (after the US open) and 22:40 (after the US
close). Two routines share this playbook ("intraday update" for the first two, "intraday update (US)" for the last two);
the clock tells you which run you are. The morning routine (`DAILY.md`) has already written the day's reports from the
data and news available at 06:00; your job is to refresh the numbers and say what has happened since. You start with a
fresh clone of this repository and no other context; read this file first, then do the steps in order. Python's standard
library is enough; Node is used for the deploy. Do not end the run before step 5 is done.

## 1. Refresh the data

    git pull --rebase origin main
    python -X utf8 pipeline/fetch.py --refresh     # 1-3 minutes; give it a 10-minute timeout
    python -X utf8 pipeline/alerts.py              # data alerts (large moves) into data/alerts.json, the News tab
    python -X utf8 pipeline/brief.py               # the numbers; live Yahoo prints include the current session

Read today's morning reports and the earlier intraday snapshots in `data/letters/YYYY-MM-DD/` (today's date, UTC) so you
know what was already said; each run adds to the story, it does not repeat it.

**If the refresh fails, the report is still written.** Yahoo sometimes refuses the cloud runs (HTTP 429, too many requests
from a shared address; 12 Nov 2026, 15:40 run). `fetch.py` then stops asking after four refusals, keeps the previous run's
prices, still writes `data/data.json`, and the brief opens with a warning line ("Yahoo refused the refresh ... as of HH:MM
UTC"). Write the report anyway: quote the brief's prices with that time, say in the summary paragraph that the market data
is from the earlier run, and take the moves since then only from linked sources (Reuters, Bloomberg, CNBC, the exchanges).
If `fetch.py` does not finish within its timeout, run `python pipeline/fetch.py --offline`: it writes the previous run's
data (taken from the live site on a fresh clone) so `alerts.py`, `brief.py` and `build.py` work. A run that ends without a
report is the worst outcome; a late or thinner report that says what it could not verify is fine.

## 2. Which run this is, and what it covers

Decide from the current Oslo time (UTC+1 in winter, UTC+2 in summer):

- **European open (about 09:05).** The Asian session and the overnight news, US index futures (the brief's "US session
  outside regular hours" section) and the first half hour in Europe and Oslo, Norwegian releases at 08:00 (Statistics
  Norway) against consensus, oil and gas, the krone. Short: what the day starts with.
- **Noon (about 12:05).** The European morning (STOXX 600, DAX, FTSE, Oslo Børs), government bonds, the krone and the
  I-44, commodities, the morning's data and speeches, and the US futures ahead of the open and of the 14:30 US releases.
- **US open (about 15:40).** The US releases at 14:30 (CPI, payrolls, claims, retail sales …) against consensus and the
  market reaction, the first minutes of US trading (the ETF proxies and futures in the brief; the index prints are live),
  Europe into the afternoon, the I-44 fix at 14:15, Treasury yields and the dollar.
- **US close (about 22:40).** The day's wrap: US closing changes (the equity rows in the brief are the closes once the
  market has closed), the after-hours prints of the index ETFs and the futures (the "after hrs" columns; write "the S&P 500
  ETF is 0.3 per cent lower after hours", never an index level), earnings and news after the bell, the European and
  Oslo closes, yields, oil, the krone at the end of the day, and what to watch overnight and tomorrow morning. This run
  is the one a reader opens next morning, so it stands on its own.

Then search the web for what has moved in that window: the equity indices, government bonds, oil, gas and metals, the
dollar, the euro and the krone (EUR/NOK, USD/NOK and the I-44 index are in the brief), macro data released today with
the outcome against consensus, central bank decisions, speeches and comments, political news with a market effect.
Always cover Norway. Every fact needs a source you can link; say what could not be verified.

Sources, in order of preference, exactly as in `DAILY.md`: (1) primary sources (central banks, statistics agencies,
exchanges, companies, governments); (2) the established financial press (Financial Times, Bloomberg, Reuters, Wall Street
Journal, CNBC, Nikkei Asia; for Norway E24, DN, Finansavisen, NRK); (3) everything else (FXStreet, Kitco, StockTitan, Trading
Economics, aggregators, brokers' blogs) only when a fact exists nowhere in tiers 1 and 2, never as the sole source for a
number, a quote or a market move, and when such a page cites a tier-1 or tier-2 source, cite that source instead.
Tone as in DAILY.md: a one- or two-sentence reading of the session so far at the top, bullets that open with their
conclusion in a short bold clause, understated verbs, small rate moves in words, numbers only where they carry the point.
Equity indices are described by their percentage change only, never by their level ("the S&P 500 is up 0.7 per cent",
not "at 7,531"); levels stay for exchange rates, yields, oil, gas and the VIX. Do not quote a figure whose observation is
older than the previous trading day (the brief marks stale rows); Dated Brent and the UK NBP gas price are weekly series a
week or more behind and stay out of the intraday report, as do the BIS effective exchange rates and the NFCI. The brief's
sinceMPR column (change since Norges Bank's last Monetary Policy Report) and the Brent futures curve are fair game.
People are named and their position described ("board member Toichiro Asada argued for a cautious pace"), never labelled
"a dovish member". **Do not use investingLive (investinglive.com) at all**, not even as a fallback: the session's moves are
on Reuters ("European shares", "Wall Street", "Oil", "Forex" wraps), Bloomberg, CNBC and the exchanges, and the numbers
themselves are in the brief. Label a citation with the outlet that wrote the piece, not the site that hosts it.

## 3. Write the intraday report, in English and in Norwegian

Write a new pair of files `data/letters/YYYY-MM-DD/intraday-HHMM.en.md` and `intraday-HHMM.no.md`, where HHMM is the
as-of time in Oslo time (for example `intraday-1545.en.md`). Never overwrite or delete an earlier snapshot: every run
adds its own pair, and the Intraday view on the page shows the last four snapshots, whatever the day, as collapsed
sections with the newest on top, each tagged with its market phase. Heading, with the phase of your run:
`# Intraday, <phase> · <weekday day month year>, HH:MM Oslo time` (Norwegian: `# Intradag, <fase> · <ukedag dag. måned
år>, kl. HH.MM`), where the phase is one of "Europe open" / "Europa åpnet" (the 09:05 run), "Europe midday" / "Europa
midt på dagen" (12:05), "US open" / "USA åpnet" (15:40) and "US close" / "USA stengt" (22:40). Example:
`# Intradag, USA stengt · torsdag 12. november 2026, kl. 22.50`. Format and tone as in DAILY.md: a bold
summary paragraph, then short sections (Equities; Rates and currencies; Commodities; Data and central banks; What to
watch next), bullets, links in the text, 250-400 words. A `{chart: ...}` line is allowed where it helps. Do not change the
other reports. The US-close run may run to 450 words, as it is the day's wrap.

If a central bank decided or a data release changed the picture, update `data/policy_rates.csv` and `data/events.csv`
as DAILY.md step 3 describes.

## 4. Build and deploy

    python -X utf8 pipeline/lint.py        # fix every finding in the intraday report, rerun until clean
    python -X utf8 build.py
    npx wrangler deploy                         # CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are in the environment; never write them into the repository

The deploy must print `https://market-summary.jskjeltorp.workers.dev`.

## 5. Commit and push

    git add -A
    git commit -m "Intraday update YYYY-MM-DD HH:MM"
    git pull --rebase origin main && git push origin main

## 6. Report

End with the summary paragraph (English), the deployed address and the commit.
