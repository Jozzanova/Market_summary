#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""alerts.py - market-moving events, kept as the last 20 in data/alerts.json.

    python -X utf8 pipeline/alerts.py                 # scan data/data.json for threshold breaches, add new ones, print them
    python -X utf8 pipeline/alerts.py --add "text (English)" --no "tekst (norsk)" --url URL [--severity high|medium] [--kind shock|fx|equity|rates|commodity|credit] [--source "Reuters"]
    python -X utf8 pipeline/alerts.py --list

Two kinds of entry: data alerts, found here from the daily changes in data/data.json (one per instrument and date, so a
move is reported once), and agent alerts, added by the alerts routine with --add when a source reports a shock the
numbers do not show yet. The page colours the Alerts tab red when a high-severity event is less than 24 hours old,
orange for medium, green otherwise. Standard library only; fetch.py must have run first.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "data.json"
ALERTS = ROOT / "data" / "alerts.json"
KEEP = 20

# symbol: (label_en, label_no, kind, threshold medium, threshold high, unit, source label, source url)
NB_FX = "https://www.norges-bank.no/en/topics/Statistics/exchange_rates/"
YQ = "https://finance.yahoo.com/quote/{}"
FRED = "https://fred.stlouisfed.org/series/{}"
RULES = {
    "nok_i44": ("the import-weighted krone index I-44", "den importveide kroneindeksen I-44", "fx", 1.0, 1.5, "%", "Norges Bank", NB_FX),
    "EURNOK=X": ("EUR/NOK", "EUR/NOK", "fx", 1.0, 1.5, "%", "Yahoo Finance", YQ.format("EURNOK%3DX")),
    "USDNOK=X": ("USD/NOK", "USD/NOK", "fx", 1.0, 1.5, "%", "Yahoo Finance", YQ.format("USDNOK%3DX")),
    "DX-Y.NYB": ("the dollar index", "dollarindeksen", "fx", 1.0, 1.5, "%", "Yahoo Finance", YQ.format("DX-Y.NYB")),
    "^GSPC": ("the S&P 500", "S&P 500", "equity", 2.0, 3.0, "%", "Yahoo Finance", YQ.format("%5EGSPC")),
    "^IXIC": ("the Nasdaq Composite", "Nasdaq Composite", "equity", 2.5, 4.0, "%", "Yahoo Finance", YQ.format("%5EIXIC")),
    "^SOX": ("the SOX semiconductor index", "halvlederindeksen SOX", "equity", 4.0, 7.0, "%", "Yahoo Finance", YQ.format("%5ESOX")),
    "^STOXX": ("the STOXX Europe 600", "STOXX Europe 600", "equity", 2.0, 3.0, "%", "Yahoo Finance", YQ.format("%5ESTOXX")),
    "OSEBX.OL": ("the Oslo Børs benchmark index", "hovedindeksen på Oslo Børs", "equity", 2.0, 3.0, "%", "Yahoo Finance", YQ.format("OSEBX.OL")),
    "^N225": ("the Nikkei 225", "Nikkei 225", "equity", 2.5, 4.0, "%", "Yahoo Finance", YQ.format("%5EN225")),
    "^KS11": ("the KOSPI", "KOSPI", "equity", 3.0, 5.0, "%", "Yahoo Finance", YQ.format("%5EKS11")),
    "^VIX": ("the VIX", "VIX", "equity", 20.0, 35.0, "%", "Yahoo Finance", YQ.format("%5EVIX")),
    "^TNX": ("the US 10-year Treasury yield", "den amerikanske tiårsrenten", "rates", 15.0, 25.0, "bp", "Yahoo Finance", YQ.format("%5ETNX")),
    "DGS2": ("the US 2-year Treasury yield", "den amerikanske toårsrenten", "rates", 15.0, 25.0, "bp", "FRED", FRED.format("DGS2")),
    "EA_SR_10Y": ("the euro-area AAA 10-year yield", "eurosonens AAA-tiårsrente", "rates", 15.0, 25.0, "bp", "ECB", "https://data.ecb.europa.eu/"),
    "nok_10y": ("the Norwegian 10-year government yield", "den norske tiårsrenten", "rates", 15.0, 25.0, "bp", "Norges Bank", "https://www.norges-bank.no/en/topics/Statistics/Interest-rates/"),
    "BZ=F": ("Brent crude", "Brent-oljen", "commodity", 5.0, 8.0, "%", "Yahoo Finance", YQ.format("BZ%3DF")),
    "TTF=F": ("Dutch TTF gas", "nederlandsk TTF-gass", "commodity", 8.0, 15.0, "%", "Yahoo Finance", YQ.format("TTF%3DF")),
    "GC=F": ("gold", "gull", "commodity", 3.0, 5.0, "%", "Yahoo Finance", YQ.format("GC%3DF")),
    "HG=F": ("copper", "kobber", "commodity", 4.0, 6.0, "%", "Yahoo Finance", YQ.format("HG%3DF")),
    "BAMLH0A0HYM2": ("the US high-yield spread", "det amerikanske high yield-påslaget", "credit", 25.0, 50.0, "bp", "FRED", FRED.format("BAMLH0A0HYM2")),
}
VERB = {"%": (("rose", "fell"), ("steg", "falt")), "bp": (("rose", "fell"), ("steg", "falt"))}


def load():
    if ALERTS.exists():
        return json.loads(ALERTS.read_text(encoding="utf-8"))
    return {"updated": None, "events": []}


def save(db):
    db["events"] = sorted(db["events"], key=lambda e: e["time"], reverse=True)[:KEEP]
    db["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ALERTS.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")


def fmt_num(x, dec):
    return f"{x:,.{dec}f}"


def scan(db):
    D = json.loads(DATA.read_text(encoding="utf-8"))
    series = {**D.get("yahoo", {}), **D.get("fred", {}), **D.get("ecb", {}), **D.get("nb", {})}
    have = {e["id"] for e in db["events"]}
    new = []
    for sym, (en, no, kind, med, high, unit, src, url) in RULES.items():
        v = series.get(sym)
        if not v or v.get("d1") is None or not v.get("last_date"):
            continue
        d1, last = float(v["d1"]), float(v["last"])
        size = abs(d1)
        if sym == "^VIX":   # the VIX: a level above 30 or a jump of 20 per cent or more
            if last < 30 and size < med:
                continue
            sev = "high" if (last >= 35 or size >= high) else "medium"
        elif sym == "BAMLH0A0HYM2":   # credit: only widening matters
            if d1 < med:
                continue
            sev = "high" if d1 >= high else "medium"
        else:
            if size < med:
                continue
            sev = "high" if size >= high else "medium"
        eid = f"{sym}:{v['last_date']}"
        if eid in have:
            continue
        dec = int(v.get("decimals", 2))
        up = d1 > 0
        if unit == "bp":
            chg_en, chg_no = f"{abs(d1):.0f} basis points to {last:.2f} per cent", f"{abs(d1):.0f} basispunkter til {last:.2f} prosent".replace(".", ",")
        else:
            chg_en, chg_no = f"{abs(d1):.1f} per cent to {fmt_num(last, dec)}", f"{abs(d1):.1f} prosent til {fmt_num(last, dec)}".replace(",", " ").replace(".", ",")
        date_en = datetime.strptime(v["last_date"], "%Y-%m-%d").strftime("%-d %B %Y") if sys.platform != "win32" else datetime.strptime(v["last_date"], "%Y-%m-%d").strftime("%d %B %Y").lstrip("0")
        live = " (live print, session not closed)" if v.get("last_time") and v["last_time"][:10] > v["last_date"] else ""
        text_en = f"{en[0].upper() + en[1:]} {VERB[unit][0][0 if up else 1]} {chg_en} on {date_en}{live}."
        text_no = f"{no[0].upper() + no[1:]} {VERB[unit][1][0 if up else 1]} {chg_no} {v['last_date']}{' (løpende kurs, handelen ikke avsluttet)' if live else ''}."
        if sym == "nok_i44" or sym in ("EURNOK=X", "USDNOK=X"):
            text_en += " A rise is a weaker krone." if up else " A fall is a stronger krone."
            text_no += " Oppgang betyr svakere krone." if up else " Nedgang betyr sterkere krone."
        t = v.get("last_time") or (v["last_date"] + " 22:00 UTC")
        time_iso = datetime.strptime(t, "%Y-%m-%d %H:%M UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
        ev = {"id": eid, "time": time_iso, "severity": sev, "kind": kind, "origin": "data", "source": src, "url": url, "text_en": text_en, "text_no": text_no,
              "value": {"symbol": sym, "last": last, "d1": d1, "unit": unit}}
        db["events"].append(ev); new.append(ev)
    return new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", help="English text of an agent alert")
    ap.add_argument("--no", help="Norwegian text")
    ap.add_argument("--url", help="source link")
    ap.add_argument("--source", default="")
    ap.add_argument("--severity", default="high", choices=["high", "medium"])
    ap.add_argument("--kind", default="shock", choices=["shock", "fx", "equity", "rates", "commodity", "credit"])
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    db = load()
    if a.add:
        if not (a.no and a.url):
            sys.exit("--add needs --no (Norwegian text) and --url (source link)")
        now = datetime.now(timezone.utc)
        eid = "agent:" + now.strftime("%Y%m%d%H%M%S")
        db["events"].append({"id": eid, "time": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "severity": a.severity, "kind": a.kind, "origin": "agent", "source": a.source or a.url.split("/")[2],
                             "url": a.url, "text_en": a.add.strip(), "text_no": a.no.strip()})
        save(db); print(f"added {eid}: {a.add.strip()[:100]}"); return 0
    if a.list:
        for e in sorted(db["events"], key=lambda e: e["time"], reverse=True):
            print(f"{e['time']} {e['severity']:<6} {e['kind']:<9} {e['text_en'][:110]}  {e['url']}")
        return 0
    new = scan(db)
    save(db)
    print(f"ALERTS: {len(new)} new data alert(s); {len(db['events'])} kept")
    for e in new:
        print(f"  {e['severity']:<6} {e['text_en']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
