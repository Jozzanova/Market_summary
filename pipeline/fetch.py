#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
fetch.py - the market data behind the market summary dashboard. Standard library only.

    python -X utf8 pipeline/fetch.py            # fetch everything (Yahoo cached 2 h, others 6 h), write data/data.json
    python -X utf8 pipeline/fetch.py --refresh  # ignore the cache

Sources (all free, no keys):
  Yahoo Finance chart API v8 (one year of daily closes for every instrument in data/instruments.csv, plus the latest print)
  FRED (US Treasury yields, TIPS, breakevens, ICE BofA credit OAS indices)
  ECB Data Portal (euro-area AAA yield curve, 2y and 10y)
  Norges Bank open data (policy rate, 3y and 10y government bond yields, 3m treasury bill)

A source that fails keeps the previous data.json's block for it (listed under kept_from_previous), so one outage does
not empty the page. Everything is in per cent for yields and index points / prices otherwise.
"""
import csv
import io
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from news import fetch_news   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA, RAW = ROOT / "data", ROOT / "rawData"
RAW.mkdir(exist_ok=True)
REFRESH = "--refresh" in sys.argv
UA = "market-summary/0.1 admin@jskjeltorp.workers.dev"
FRED_SERIES = {"DGS2": "US 2-year", "DGS10": "US 10-year", "DGS30": "US 30-year", "DFII10": "US 10-year real (TIPS)", "T10YIE": "US 10-year breakeven",
               "BAMLC0A0CM": "US IG OAS", "BAMLH0A0HYM2": "US HY OAS", "BAMLHE00EHYIOAS": "Euro HY OAS", "BAMLEMCBPIOAS": "EM corporate OAS"}
ECB_YC = {"SR_2Y": "Euro area AAA 2-year", "SR_10Y": "Euro area AAA 10-year"}
NB_SERIES = {"B.10Y.GBON": ("nok_10y", "Norway 10-year government bond"), "B.3Y.GBON": ("nok_3y", "Norway 3-year government bond"),
             "B.3M.TBIL": ("nok_3m", "Norway 3-month treasury bill")}


def log(msg):
    print(msg, flush=True)


def get(url, retries=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/csv,text/plain,*/*"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries:
                time.sleep(6 * (attempt + 1))
                continue
            raise


def cached(name, max_age_h, producer):
    """Raw bytes cached under rawData/ for max_age_h hours (ignored with --refresh)."""
    path = RAW / name
    if path.exists() and not REFRESH and time.time() - path.stat().st_mtime < max_age_h * 3600:
        return path.read_bytes()
    data = producer()
    path.write_bytes(data)
    return data


# ----------------------------------------------------------------------------------------------------------------------
# Yahoo
# ----------------------------------------------------------------------------------------------------------------------
def yahoo_chart(sym):
    raw = cached(f"yahoo_{sym.replace('^', '_').replace('=', '_').replace('/', '_')}.json", 2,
                 lambda: get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sym)}?range=1y&interval=1d"))
    res = json.loads(raw)["chart"]["result"][0]
    meta, ts = res["meta"], res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    tz = meta.get("exchangeTimezoneName") or "UTC"
    dates, vals = [], []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        dates.append(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"))
        vals.append(round(float(c), 6))
    # the latest print (may be an intraday value for a market that is open now)
    last = meta.get("regularMarketPrice")
    last_t = meta.get("regularMarketTime")
    return {"name": meta.get("shortName") or meta.get("longName") or sym, "currency": meta.get("currency"), "tz": tz,
            "dates": dates, "values": vals, "last": last, "last_time": datetime.fromtimestamp(last_t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if last_t else None,
            "prev_close": meta.get("previousClose") or meta.get("chartPreviousClose")}


def pct(a, b):
    return None if a is None or b in (None, 0) else round(100 * (a / b - 1), 3)


def stats(s, kind):
    """Changes over 1 day, 1 week, 1 month, year to date. Yields and rates in basis points, the rest in per cent."""
    d, v = s["dates"], s["values"]
    if len(v) < 2:
        return {}
    last, last_date = v[-1], d[-1]
    # the last daily close is taken as the "latest" value; if Yahoo reports a later live print, that is used instead
    if s.get("last") is not None and s.get("last_time") and s["last_time"][:10] > last_date:
        last = float(s["last"])
    prev = v[-2] if (s.get("last") is None or s["last_time"][:10] <= last_date) else v[-1]
    def back(days):
        target = (datetime.strptime(last_date, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
        for i in range(len(d) - 1, -1, -1):
            if d[i] <= target:
                return v[i]
        return None
    year = last_date[:4]
    ytd_base = None
    for i in range(len(d)):
        if d[i][:4] == year:
            ytd_base = v[i - 1] if i > 0 else None
            break
    if kind == "yield":
        ch = lambda base: None if base is None else round(100 * (last - base), 1)   # basis points
    else:
        ch = lambda base: pct(last, base)
    hi, lo = max(v[-252:]), min(v[-252:])
    return {"last": last, "last_date": last_date if (s.get("last") is None or s["last_time"][:10] <= last_date) else s["last_time"][:10],
            "prev": prev, "d1": ch(prev), "w1": ch(back(7)), "m1": ch(back(30)), "ytd": ch(ytd_base), "hi52": hi, "lo52": lo,
            "unit": "bp" if kind == "yield" else "%"}


def instruments():
    with open(DATA / "instruments.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def fetch_yahoo(previous):
    out, failed = {}, []
    for ins in instruments():
        sym = ins["symbol"]
        try:
            s = yahoo_chart(sym)
            out[sym] = {**{k: ins[k] for k in ("group", "name_en", "name_no", "kind", "decimals")}, "currency": s["currency"], "last_time": s["last_time"],
                        "dates": s["dates"][-260:], "values": s["values"][-260:], **stats(s, ins["kind"])}
        except Exception as e:
            failed.append(sym)
            log(f"  !! yahoo {sym}: {repr(e)[:120]}")
            if sym in previous:
                out[sym] = previous[sym]
    log(f"yahoo: {len(out) - len(failed)} fetched, {len(failed)} failed{' (' + ', '.join(failed) + ')' if failed else ''}")
    return out, failed


# ----------------------------------------------------------------------------------------------------------------------
# FRED, ECB, Norges Bank
# ----------------------------------------------------------------------------------------------------------------------
def parse_csv_tables(raw):
    """fredgraph.csv returns a CSV, or a zip with several CSVs; return {series: [(date, value)]}."""
    texts = []
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            texts = [z.read(n).decode("utf-8", "ignore") for n in z.namelist() if n.endswith(".csv")]
    else:
        texts = [raw.decode("utf-8", "ignore")]
    series = {}
    for t in texts:
        rows = list(csv.reader(io.StringIO(t)))
        if not rows or not rows[0] or rows[0][0].lower() not in ("observation_date", "date"):
            continue
        for j, name in enumerate(rows[0][1:], start=1):
            name = name.split("_")[0] if name.endswith("_CLOSE") else name
            if name not in series:
                series[name] = []
            for r in rows[1:]:
                if len(r) > j and r[j] not in ("", "."):
                    series[name].append((r[0], float(r[j])))
    return {k: sorted(set(v)) for k, v in series.items()}


def fetch_fred():
    start = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
    raw = cached("fred.csv", 6, lambda: get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={','.join(FRED_SERIES)}&cosd={start}"))
    tables = parse_csv_tables(raw)
    out = {}
    for sid, label in FRED_SERIES.items():
        obs = [o for o in tables.get(sid, []) if o[0] >= start]
        if obs:
            s = {"dates": [o[0] for o in obs], "values": [o[1] for o in obs]}
            out[sid] = {"name_en": label, "group": "credit" if "OAS" in label else "rates", "kind": "yield", "decimals": 2, "dates": s["dates"], "values": s["values"], **stats(s, "yield")}
    log(f"fred: {len(out)} series, latest {max(v['last_date'] for v in out.values()) if out else 'none'}")
    return out


def fetch_ecb():
    out = {}
    for key, label in ECB_YC.items():
        raw = cached(f"ecb_{key}.csv", 6, lambda key=key: get(f"https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.{key}?format=csvdata&lastNObservations=280"))
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "ignore"))))
        obs = sorted((r["TIME_PERIOD"], float(r["OBS_VALUE"])) for r in rows if r.get("OBS_VALUE"))
        s = {"dates": [o[0] for o in obs], "values": [o[1] for o in obs]}
        out["EA_" + key] = {"name_en": label, "group": "rates", "kind": "yield", "decimals": 2, **s, **stats(s, "yield")}
    log(f"ecb: {len(out)} series, latest {max(v['last_date'] for v in out.values())}")
    return out


def fetch_nb():
    out = {}
    for key, (sid, label) in NB_SERIES.items():
        raw = cached(f"nb_{sid}.csv", 6, lambda key=key: get(f"https://data.norges-bank.no/api/data/GOVT_GENERIC_RATES/{key}?format=csv&lastNObservations=280"))
        obs = sorted((r["TIME_PERIOD"], float(r["OBS_VALUE"])) for r in csv.DictReader(io.StringIO(raw.decode("utf-8", "ignore")), delimiter=";") if r.get("OBS_VALUE"))
        s = {"dates": [o[0] for o in obs], "values": [o[1] for o in obs]}
        out[sid] = {"name_en": label, "group": "rates", "kind": "yield", "decimals": 2, **s, **stats(s, "yield")}
    raw = cached("nb_policy.csv", 6, lambda: get("https://data.norges-bank.no/api/data/IR/B.KPRA.SD.?format=csv&lastNObservations=280"))
    obs = sorted((r["TIME_PERIOD"], float(r["OBS_VALUE"])) for r in csv.DictReader(io.StringIO(raw.decode("utf-8", "ignore")), delimiter=";") if r.get("OBS_VALUE"))
    s = {"dates": [o[0] for o in obs], "values": [o[1] for o in obs]}
    out["nok_policy"] = {"name_en": "Norges Bank policy rate", "group": "rates", "kind": "yield", "decimals": 2, **s, **stats(s, "yield")}
    # the import-weighted krone index I-44 (1995 = 100; a rise is a weaker krone), Norges Bank's own measure of the krone
    raw = cached("nb_i44.csv", 6, lambda: get("https://data.norges-bank.no/api/data/EXR/B.I44.NOK.SP?format=csv&lastNObservations=280"))
    obs = sorted((r["TIME_PERIOD"], float(r["OBS_VALUE"])) for r in csv.DictReader(io.StringIO(raw.decode("utf-8", "ignore")), delimiter=";") if r.get("OBS_VALUE"))
    s = {"dates": [o[0] for o in obs], "values": [o[1] for o in obs]}
    out["nok_i44"] = {"name_en": "Krone index I-44 (up = weaker krone)", "name_no": "Kroneindeksen I-44 (opp = svakere krone)", "group": "fx", "kind": "price", "decimals": 2, **s, **stats(s, "price")}
    log(f"norges bank: {len(out)} series, latest {max(v['last_date'] for v in out.values())}; I-44 {out['nok_i44']['last']} ({out['nok_i44']['last_date']})")
    return out


def fetch_ssb():
    """Norwegian CPI and CPI-ATE, 12-month rates, monthly (Statistics Norway table 14706)."""
    def producer():
        q = {"query": [{"code": "KPIavledetSerie", "selection": {"filter": "item", "values": ["KPI", "KPI-JAE"]}},
                       {"code": "ContentsCode", "selection": {"filter": "item", "values": ["Tolvmanedersendring"]}},
                       {"code": "Tid", "selection": {"filter": "top", "values": ["60"]}}], "response": {"format": "json-stat2"}}
        req = urllib.request.Request("https://data.ssb.no/api/v0/en/table/14706", data=json.dumps(q).encode(), headers={"User-Agent": UA, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    j = json.loads(cached("ssb_14706.json", 6, producer))
    dims = j["dimension"]; series_codes = list(dims["KPIavledetSerie"]["category"]["index"]); months = list(dims["Tid"]["category"]["index"])
    vals = j["value"]; n_t = len(months)
    out = {}
    for i, code in enumerate(series_codes):
        obs = [(m[:4] + "-" + m[5:7] + "-01", vals[i * n_t + k]) for k, m in enumerate(months) if vals[i * n_t + k] is not None]
        s = {"dates": [o[0] for o in obs], "values": [float(o[1]) for o in obs]}
        key = "cpi_12m" if code == "KPI" else "cpiate_12m"
        out[key] = {"name_en": "Norway CPI, 12-month change" if code == "KPI" else "Norway CPI-ATE, 12-month change", "name_no": "KPI, tolvmånedersvekst" if code == "KPI" else "KPI-JAE, tolvmånedersvekst",
                    "group": "norway", "kind": "yield", "decimals": 1, **s, "last": s["values"][-1], "last_date": s["dates"][-1], "prev": s["values"][-2] if len(s["values"]) > 1 else None, "unit": "pp"}
    log(f"ssb: CPI {out['cpi_12m']['last']}% and CPI-ATE {out['cpiate_12m']['last']}% in {out['cpi_12m']['last_date'][:7]}")
    return out


# ----------------------------------------------------------------------------------------------------------------------
def read_csv(name):
    p = DATA / name
    if not p.exists():
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main():
    prev = json.loads((DATA / "data.json").read_text(encoding="utf-8")) if (DATA / "data.json").exists() else {}
    kept = []
    log("1. Yahoo")
    yahoo, failed = fetch_yahoo(prev.get("yahoo", {}))
    kept += [f"yahoo:{s}" for s in failed if s in yahoo]
    blocks = {}
    for name, fn in (("fred", fetch_fred), ("ecb", fetch_ecb), ("nb", fetch_nb), ("ssb", fetch_ssb), ("news", fetch_news)):
        log(f"{name}")
        try:
            blocks[name] = fn()
        except Exception as e:
            log(f"  !! {name} failed: {repr(e)[:200]}; keeping the previous data")
            blocks[name] = prev.get(name, {})
            kept.append(name)
    data = {"asof": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "yahoo": yahoo, **blocks,
            "policy_rates": read_csv("policy_rates.csv"), "events": read_csv("events.csv"), "kept_from_previous": kept}
    (DATA / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"wrote data/data.json ({(DATA / 'data.json').stat().st_size // 1024} kB); kept from previous: {kept or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
