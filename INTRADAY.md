# Intraday update routine

This file is the playbook for the scheduled cloud agent that writes the intraday report on weekdays, around 12:05 and
17:05 Oslo time. The morning routine (`DAILY.md`) has already written the day's four reports from the data and news
available at 06:00; your job is to refresh the numbers and say what has happened in the session since then. You start
with a fresh clone of this repository and no other context; read this file first, then do the steps in order. Python's
standard library is enough; Node is used for the deploy. Do not end the run before step 5 is done.

## 1. Refresh the data

    git pull --rebase origin main
    python -X utf8 pipeline/fetch.py --refresh     # 1-3 minutes; give it a 10-minute timeout
    python -X utf8 pipeline/alerts.py              # data alerts (large moves) into data/alerts.json, the Alerts tab
    python -X utf8 pipeline/brief.py               # the numbers; live Yahoo prints include the current session

Read today's morning reports in `data/letters/YYYY-MM-DD/` (today's date, UTC) so you know what was already said.

## 2. Read the news since this morning

Search the web for what has moved since 06:00 Oslo time: the European session (STOXX 600, DAX, FTSE, Oslo Børs), US
futures and, in the afternoon run, the US open and the first hours of trading; government bonds; oil, gas and metals;
the dollar, the euro and the krone (EUR/NOK, USD/NOK and the I-44 index are in the brief); macro data released today
with the outcome against consensus; central bank decisions, speeches and comments; political news with a market
effect. Always cover Norway. Every fact needs a source you can link; say what could not be verified.

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
**Do not use investingLive (investinglive.com) at all**, not even as a fallback: the session's moves are on Reuters
("European shares", "Wall Street", "Oil", "Forex" wraps), Bloomberg, CNBC and the exchanges, and the numbers themselves
are in the brief. Label a citation with the outlet that wrote the piece, not the site that hosts it.

## 3. Write the intraday report, in English and in Norwegian

Write a new pair of files `data/letters/YYYY-MM-DD/intraday-HHMM.en.md` and `intraday-HHMM.no.md`, where HHMM is the
as-of time in Oslo time (for example `intraday-1205.en.md`). Never overwrite or delete an earlier snapshot: every run
adds its own pair, and the page shows all of the day's snapshots as collapsible sections, newest first and open. The
afternoon report covers the whole day so far. Heading: `# Intraday update, <weekday day month year>, as of HH:MM
Oslo time` (Norwegian: `# Intradagoppdatering, <ukedag dag. måned år>, per kl. HH.MM`). Format and tone as in DAILY.md:
a bold summary paragraph, then short sections (Equities; Rates and currencies; Commodities; Data and central banks;
What to watch for the rest of the day), bullets, links in the text, 250-400 words. A `{chart: ...}` line is allowed
where it helps. Do not change the other reports.

If a central bank decided or a data release changed the picture, update `data/policy_rates.csv` and `data/events.csv`
as DAILY.md step 3 describes.

## 4. Build and deploy

    python -X utf8 build.py
    npx wrangler deploy                         # CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are in the environment; never write them into the repository

The deploy must print `https://market-summary.jskjeltorp.workers.dev`.

## 5. Commit and push

    git add -A
    git commit -m "Intraday update YYYY-MM-DD HH:MM"
    git pull --rebase origin main && git push origin main

## 6. Report

End with the summary paragraph (English), the deployed address and the commit.
