# Intraday update routine

This file is the playbook for the scheduled cloud agent that writes the intraday letter on weekdays, around 12:05 and
17:05 Oslo time. The morning routine (`DAILY.md`) has already written the day's four letters from the data and news
available at 06:00; your job is to refresh the numbers and say what has happened in the session since then. You start
with a fresh clone of this repository and no other context; read this file first, then do the steps in order. Python's
standard library is enough; Node is used for the deploy. Do not end the run before step 5 is done.

## 1. Refresh the data

    git pull --rebase origin main
    python -X utf8 pipeline/fetch.py --refresh     # 1-3 minutes; give it a 10-minute timeout
    python -X utf8 pipeline/brief.py               # the numbers; live Yahoo prints include the current session

Read today's morning letters in `data/letters/YYYY-MM-DD/` (today's date, UTC) so you know what was already said.

## 2. Read the news since this morning

Search the web for what has moved since 06:00 Oslo time: the European session (STOXX 600, DAX, FTSE, Oslo Børs), US
futures and, in the afternoon run, the US open and the first hours of trading; government bonds; oil, gas and metals;
the dollar, the euro and the krone (EUR/NOK, USD/NOK and the I-44 index are in the brief); macro data released today
with the outcome against consensus; central bank decisions, speeches and comments; political news with a market
effect. Always cover Norway. Every fact needs a source you can link; say what could not be verified.

## 3. Write the intraday letter, in English and in Norwegian

Write `data/letters/YYYY-MM-DD/intraday.en.md` and `intraday.no.md` (overwrite the midday version in the afternoon run;
the afternoon letter covers the whole day so far). Heading: `# Intraday update, <weekday day month year>, as of HH:MM
Oslo time` (Norwegian: `# Intradagoppdatering, <ukedag dag. måned år>, per kl. HH.MM`). Format and tone as in DAILY.md:
a bold summary paragraph, then short sections (Equities; Rates and currencies; Commodities; Data and central banks;
What to watch for the rest of the day), bullets, links in the text, 250-400 words. A `{chart: ...}` line is allowed
where it helps. Do not change the other letters.

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
