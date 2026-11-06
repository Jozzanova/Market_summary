# Alerts routine

This file is the playbook for the scheduled cloud agent that judges whether a market-moving event has happened. A
worker on the site (`src/worker.js`) already sniffs every five minutes: it flags large moves in the krone (I-44, EUR/NOK,
USD/NOK), the main equity indices, the VIX, US yields, oil, gas and metals, and headlines with shock keywords, and keeps
the last 20 in a live list (https://market-summary.jskjeltorp.workers.dev/api/alerts). Your job is the judgement the
keyword scan cannot make: is there a genuinely market-moving event in the last two hours that the list does not carry,
and is anything on it wrong? Keep the run short; most runs end with nothing to add.

## 1. Look

    git pull --rebase origin main
    curl -s https://market-summary.jskjeltorp.workers.dev/api/alerts | python3 -m json.tool | head -120
    python -X utf8 pipeline/alerts.py --list

Then read the latest headlines (FT, WSJ, Bloomberg, Reuters, CNBC; E24, DN, Finansavisen for Norway) and search the
web for breaking news of the last two hours. A market-moving event is one of: an unscheduled central bank action or a
decision that surprised the consensus; a sovereign, bank or large corporate default or rescue; a trading halt or
exchange outage; a geopolitical escalation with a market effect (attack, invasion, blockade of shipping, sanctions
with immediate effect); a sudden political shock (resignation of a head of government or finance minister, a failed
vote with fiscal consequences, capital controls); a move in the krone, Oslo Børs, oil or a major index of the size the
worker flags, when its cause is known and worth a line.

## 2. Add an alert only when it is warranted

    python -X utf8 pipeline/alerts.py --add "One or two sentences in English: what happened, when, and the market effect." --no "Det samme på norsk." --url "https://..." --source "Reuters" --severity high --kind shock

Rules: one alert per event; a primary or established source with a link (central bank, exchange, Reuters, Bloomberg,
FT, WSJ, E24, DN); state the time of the event; severity `high` for the categories above, `medium` for a large move
with a known cause; never speculate; never add routine news, earnings, or ordinary daily moves. Do not edit or remove
the worker's entries; if one is clearly wrong (a bad print), say so in the report.

## 3. Publish only when something was added

If you added nothing, end the run without committing or deploying, with a one-line report "no market-moving event".
If you added an alert:

    python -X utf8 build.py
    npx wrangler deploy                         # CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are in the environment; never write them into files
    git add data/alerts.json && git commit -m "Alert YYYY-MM-DD HH:MM: <short title>" && git pull --rebase origin main && git push origin main

End with the alert text and its source link.
