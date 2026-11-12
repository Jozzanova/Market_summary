#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""brief.py - the numbers for today's letters, printed as text for the agent that writes them.

    python -X utf8 pipeline/brief.py

Reads data/data.json (written by fetch.py), data/policy_rates.csv, data/events.csv and the letters folder."""
import json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = json.loads((ROOT / "data" / "data.json").read_text(encoding="utf-8"))


def f(x, dec=2):
    return "n/a" if x is None else f"{x:,.{dec}f}"


def ch(v, unit):
    if v is None:
        return "n/a"
    return f"{v:+.1f} bp" if unit == "bp" else f"{v:+.2f}" if unit == "pt" else f"{v:+.2f}%"


STALE_DAYS = 4      # an observation older than this many days before the data date is marked, and must not be quoted as current


MPR_LAST = (D.get("mpr") or {}).get("last")      # the publication date of Norges Bank's last Monetary Policy Report


def since_mpr(v):
    """The change since the last Monetary Policy Report, from the last observation on or before its date."""
    if not MPR_LAST or not v.get("dates") or v.get("last") is None:
        return None
    base = None
    for d, x in zip(v["dates"], v["values"]):
        if d <= MPR_LAST:
            base = x
        else:
            break
    if base is None:
        return None
    u = v.get("unit")
    return round(100 * (v["last"] - base), 1) if u == "bp" else round(v["last"] - base, 3) if u == "pt" else (None if not base else round(100 * (v["last"] / base - 1), 3))


def table(rows, title):
    print(f"\n## {title}")
    print(f"{'instrument':<40} {'last':>12} {'date':>11} {'1d':>10} {'1w':>10} {'1m':>10} {'ytd':>10} {'sinceMPR':>10}")
    asof = D.get("asof", "")[:10]
    for name, v in rows:
        dec = int(v.get("decimals", 2))
        d = v.get("last_date", "")
        stale = "  STALE: do not quote" if d and asof and (date.fromisoformat(asof) - date.fromisoformat(d[:10])).days > STALE_DAYS else ""
        print(f"{name:<40} {f(v.get('last'), dec):>12} {d:>11} {ch(v.get('d1'), v.get('unit')):>10} {ch(v.get('w1'), v.get('unit')):>10} {ch(v.get('m1'), v.get('unit')):>10} {ch(v.get('ytd'), v.get('unit')):>10} {ch(since_mpr(v), v.get('unit')):>10}{stale}")


def main():
    now = datetime.now(timezone.utc)
    print(f"# Market brief, data as of {D['asof']} (now {now.strftime('%Y-%m-%d %H:%M UTC')})")
    if now.weekday() == 0:
        print("Monday: there has been no session since Friday. The 1d column is Friday against Thursday; the 1w column (Friday close against the previous Friday) is the week's change for the weekly review.")
    if D.get("kept_from_previous"):
        print(f"kept from the previous run (source down): {', '.join(D['kept_from_previous'])}")
    if D.get("yahoo_asof") and D["yahoo_asof"] != D["asof"]:
        print(f"!! Yahoo refused the refresh (rate limit): the prices below are from the previous run, as of {D['yahoo_asof']}. Say so in the report, "
              f"quote them with that time, and take any later move only from a linked source.")
    y = D["yahoo"]
    for grp, title in (("equity", "Equity indices (local currency; 1d = last close vs previous close, or the live print if the market is open)"),
                       ("ai", "AI and semiconductor proxies (SOX, NYSE FANG+, semiconductor, Magnificent 7 and AI ETFs)"), ("sector", "US sectors (S&P 500 sector ETFs)"), ("rates", "US Treasury yields and futures (Yahoo, live prints)"),
                       ("commodity", "Commodities"), ("fx", "FX (quoted as named; a rise in EUR/NOK is a weaker krone)")):
        table([(v["name_en"], v) for s, v in y.items() if v["group"] == grp], title)
    us = D.get("us_session", {})
    if us.get("etf"):
        print("\n## US session outside regular hours (Yahoo; the ETFs are proxies, so write 'the S&P 500 ETF after hours', never an index level; times UTC, Oslo = UTC+1 in winter, +2 in summer)")
        print(f"{'proxy':<26} {'close':>9} {'close time':>17} {'1d':>8} {'after hrs':>10} {'time':>17} {'vs close':>9} {'pre-mkt':>9} {'time':>17} {'vs close':>8}")
        for v in us["etf"].values():
            print(f"{v['name_en']:<26} {f(v.get('close')):>9} {v.get('close_time') or '':>17} {ch(v.get('d1'), '%'):>8} {f(v.get('post')):>10} {v.get('post_time') or '':>17} {ch(v.get('post_chg'), '%'):>9} {f(v.get('pre')):>9} {v.get('pre_time') or '':>17} {ch(v.get('pre_chg'), '%'):>8}")
        if us.get("futures"):
            print("Index futures (near round-the-clock trading; 1d = last print vs the last close):")
            for v in us["futures"].values():
                print(f"{v['name_en']:<26} {f(v.get('last')):>9} {v.get('last_time') or '':>17} {ch(v.get('d1'), '%'):>8}")
    table([(v["name_en"], v) for v in D.get("fred", {}).values() if v["group"] == "rates"] + [(v["name_en"], v) for v in D.get("ecb", {}).values()] + [(v["name_en"], v) for v in D.get("nb", {}).values()],
          "Government yields and policy rate, official daily fixes (FRED, ECB AAA curve, Norges Bank; one day behind the live prints)")
    table([(v["name_en"], v) for v in D.get("fred", {}).values() if v["group"] == "credit"], "Credit spreads, ICE BofA option-adjusted spreads in per cent (FRED)")
    if MPR_LAST:
        print(f"\nNorges Bank's last Monetary Policy Report was published {MPR_LAST}; the sinceMPR column is the change since then.")
    gov = D.get("gov", {})
    table([(v["name_en"], v) for v in gov.values() if v["group"] == "rates"], "Government yields, Germany and Japan (Bundesbank term structure, Ministry of Finance JGB file; official daily)")
    dv = D.get("derived", {})
    table([(v["name_en"], v) for v in dv.values()], "Derived: 5y5y forwards (2 x 10-year minus 5-year) and the euro-area periphery spread (all issuers minus AAA, 10-year, pp)")
    table([(v["name_en"], v) for src in (D.get("fred", {}), D.get("ecb", {}), gov) for v in src.values() if v["group"] == "money"], "Overnight rates (SOFR, euro short-term rate, SONIA)")
    table([(v["name_en"], v) for v in D.get("fred", {}).values() if v["group"] == "conditions"], "Financial conditions, Chicago Fed NFCI (weekly, lagged; shown on the page only, never in the letters; positive = tighter than average)")
    eer = D.get("eer", {})
    if eer:
        table([(v["name_en"], v) for v in eer.values()], "Effective exchange rates, BIS nominal broad (2020 = 100, up = stronger; LAGGED about a week: shown on the page with dates, never in the letters)")
    cv = D.get("curves", {})
    if cv:
        print("\n## Brent and WTI futures curves (USD/bbl; delivery month: price today, a week ago, at the last MPR)")
        for root, c in cv.items():
            ks = c.get("contracts", [])
            if not ks:
                continue
            asof = max(k["dates"][-1] for k in ks)
            def at(k, d):
                x = None
                for dd, v in zip(k["dates"], k["values"]):
                    if dd <= d:
                        x = v
                    else:
                        break
                return x
            wk = (date.fromisoformat(asof) - timedelta(days=7)).isoformat()
            picks = [ks[0]] + [k for i, k in enumerate(ks) if i in (5, 11, 23)]
            print(f"  {c['name_en']} (as of {asof}): " + "; ".join(f"{k['m']} {f(at(k, asof))} (week ago {f(at(k, wk))}, MPR {f(at(k, MPR_LAST)) if MPR_LAST else 'n/a'})" for k in picks))
    us = D.get("macro", {}).get("series", {}).get("USA", {})
    if "pce" in us:
        g = lambda k: f"{us[k]['last']:.1f}% ({us[k]['last_date'][:7]})" if k in us else "n/a"
        print(f"\n## US inflation, 12-month change\n  CPI {g('cpi')}, core CPI {g('core')} (OECD); PCE {g('pce')}, core PCE {g('pce_core')} (BEA via FRED)")
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
    if "nok_12m" in nb:
        d12 = [f"NOK-euro {100 * (nb['nok_12m']['last'] - ecb['EA_SR_1Y']['last']):+.0f} bp" if "EA_SR_1Y" in ecb else "", f"NOK-US {100 * (nb['nok_12m']['last'] - fr['DGS1']['last']):+.0f} bp" if "DGS1" in fr else ""]
        print(f"  12-month differentials in government rates, stand-in for the swap differential (Norway 12m bill {nb['nok_12m']['last']:.2f}% at {nb['nok_12m']['last_date']}): " + "; ".join(x for x in d12 if x))
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
    print(f"\n## Latest headlines (RSS, {news.get('fetched', 'n/a')}; newest first, time UTC; open the link before using a fact)")
    for it in news.get("items", [])[:60]:
        print(f"  {(it.get('time') or '')[:16]:<16} {it['source']:<14} {it['title'][:110]}  {it['link']}")
        if it.get("snippet"):
            print(f"  {'':<31} {it['snippet'][:160]}")
    letters = sorted(p.name for p in (ROOT / "data" / "letters").iterdir() if p.is_dir())
    print(f"\n## Letters on file: {', '.join(letters[-5:]) if letters else 'none'} (today is {today})")


if __name__ == "__main__":
    main()
