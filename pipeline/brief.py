#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""brief.py - the numbers for today's letters, printed as text for the agent that writes them.

    python -X utf8 pipeline/brief.py

Reads data/data.json (written by fetch.py), data/policy_rates.csv, data/events.csv and the letters folder."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = json.loads((ROOT / "data" / "data.json").read_text(encoding="utf-8"))


def f(x, dec=2):
    return "n/a" if x is None else f"{x:,.{dec}f}"


def ch(v, unit):
    if v is None:
        return "n/a"
    return f"{v:+.1f} bp" if unit == "bp" else f"{v:+.2f}%"


def table(rows, title):
    print(f"\n## {title}")
    print(f"{'instrument':<40} {'last':>12} {'date':>11} {'1d':>10} {'1w':>10} {'1m':>10} {'ytd':>10}")
    for name, v in rows:
        dec = int(v.get("decimals", 2))
        print(f"{name:<40} {f(v.get('last'), dec):>12} {v.get('last_date', ''):>11} {ch(v.get('d1'), v.get('unit')):>10} {ch(v.get('w1'), v.get('unit')):>10} {ch(v.get('m1'), v.get('unit')):>10} {ch(v.get('ytd'), v.get('unit')):>10}")


def main():
    now = datetime.now(timezone.utc)
    print(f"# Market brief, data as of {D['asof']} (now {now.strftime('%Y-%m-%d %H:%M UTC')})")
    if D.get("kept_from_previous"):
        print(f"kept from the previous run (source down): {', '.join(D['kept_from_previous'])}")
    y = D["yahoo"]
    for grp, title in (("equity", "Equity indices (local currency; 1d = last close vs previous close, or the live print if the market is open)"),
                       ("ai", "AI and semiconductor proxies (SOX, NYSE FANG+, semiconductor, Magnificent 7 and AI ETFs)"), ("sector", "US sectors (S&P 500 sector ETFs)"), ("rates", "US Treasury yields and futures (Yahoo, live prints)"),
                       ("commodity", "Commodities"), ("fx", "FX (quoted as named; a rise in EUR/NOK is a weaker krone)")):
        table([(v["name_en"], v) for s, v in y.items() if v["group"] == grp], title)
    table([(v["name_en"], v) for v in D.get("fred", {}).values() if v["group"] == "rates"] + [(v["name_en"], v) for v in D.get("ecb", {}).values()] + [(v["name_en"], v) for v in D.get("nb", {}).values()],
          "Government yields and policy rate, official daily fixes (FRED, ECB AAA curve, Norges Bank; one day behind the live prints)")
    table([(v["name_en"], v) for v in D.get("fred", {}).values() if v["group"] == "credit"], "Credit spreads, ICE BofA option-adjusted spreads in per cent (FRED)")
    # movers
    movers = sorted(((v["d1"], v["name_en"], v["group"]) for v in y.values() if v.get("d1") is not None and v["kind"] != "yield" and v["group"] != "sector"), key=lambda t: -abs(t[0]))
    print("\n## Largest one-day moves (prices and FX)")
    for d1, name, grp in movers[:12]:
        print(f"  {d1:+7.2f}%  {name} ({grp})")
    # curves
    fr = D.get("fred", {})
    if "DGS2" in fr and "DGS10" in fr:
        print(f"\n## Curve and real yields\n  US 2s10s: {100 * (fr['DGS10']['last'] - fr['DGS2']['last']):+.0f} bp ({fr['DGS10']['last_date']}); 10y real {f(fr.get('DFII10', {}).get('last'))}%, breakeven {f(fr.get('T10YIE', {}).get('last'))}%")
    ecb, nb = D.get("ecb", {}), D.get("nb", {})
    if "EA_SR_10Y" in ecb and "DGS10" in fr:
        print(f"  US-euro 10y spread: {100 * (fr['DGS10']['last'] - ecb['EA_SR_10Y']['last']):+.0f} bp; NOK-euro 10y spread: {100 * (nb['nok_10y']['last'] - ecb['EA_SR_10Y']['last']):+.0f} bp" if "nok_10y" in nb else "")
    print("\n## Policy rates on file (data/policy_rates.csv; verify after any meeting)")
    for r in D.get("policy_rates", []):
        print(f"  {r['bank']:<24} {r['rate']:<12} last {r['last_decision']} ({r['last_move']}), next {r['next_meeting']}")
    print("\n## Upcoming events on file (data/events.csv, next 21 days)")
    horizon = (now + timedelta(days=21)).strftime("%Y-%m-%d")
    today = now.strftime("%Y-%m-%d")
    for e in sorted(D.get("events", []), key=lambda e: (e["date"], e.get("time_oslo", ""))):
        if today <= e["date"] <= horizon:
            print(f"  {e['date']} {e.get('time_oslo', ''):>5} {e['region']:<6} {e['category']:<12} {e['event']} [{e['importance']}]")
    news = D.get("news", {})
    print(f"
## Latest headlines (RSS, {news.get('fetched', 'n/a')}; newest first, time UTC; open the link before using a fact)")
    for it in news.get("items", [])[:60]:
        print(f"  {(it.get('time') or '')[:16]:<16} {it['source']:<14} {it['title'][:110]}  {it['link']}")
        if it.get("snippet"):
            print(f"  {'':<31} {it['snippet'][:160]}")
    letters = sorted(p.name for p in (ROOT / "data" / "letters").iterdir() if p.is_dir())
    print(f"\n## Letters on file: {', '.join(letters[-5:]) if letters else 'none'} (today is {today})")


if __name__ == "__main__":
    main()
