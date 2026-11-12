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
  BIS central bank policy rates (daily, the nine banks in data/policy_rates.csv; one year, extended with the table)
  CME 30-day Fed funds futures via Yahoo (ZQ contracts): the priced change and hike/cut probability for the next two FOMC meetings

A source that fails keeps the previous data.json's block for it (listed under kept_from_previous), so one outage does
not empty the page. Everything is in per cent for yields and index points / prices otherwise.
"""
import csv
import io
import json
import re
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
FRED_SERIES = {"DGS1": "US 1-year", "DGS2": "US 2-year", "DGS5": "US 5-year", "DGS10": "US 10-year", "DGS30": "US 30-year", "DFII10": "US 10-year real (TIPS)", "T10YIE": "US 10-year breakeven",
               "T5YIFR": "US 5y5y inflation expectation", "SOFR": "SOFR (US overnight)", "NFCI": "Chicago Fed financial conditions (NFCI, weekly)",
               "BAMLC0A0CM": "US IG OAS", "BAMLH0A0HYM2": "US HY OAS", "BAMLHE00EHYIOAS": "Euro HY OAS", "BAMLEMCBPIOAS": "EM corporate OAS"}
FRED_GROUP = {"SOFR": "money", "NFCI": "conditions"}          # the rest: "credit" for the OAS series, "rates" otherwise
ECB_YC = {"SR_1Y": "Euro area AAA 1-year", "SR_2Y": "Euro area AAA 2-year", "SR_5Y": "Euro area AAA 5-year", "SR_10Y": "Euro area AAA 10-year", "SR_30Y": "Euro area AAA 30-year"}
ECB_YC_ALL = {"SR_2Y": "Euro area all issuers 2-year", "SR_10Y": "Euro area all issuers 10-year"}      # the ECB's second curve, every euro-area government
# BIS nominal effective exchange rates, broad basket (64 economies), daily; published with a lag of about a week
EER_AREAS = {"US": ("US dollar", "Amerikanske dollar"), "XM": ("Euro", "Euro"), "JP": ("Japanese yen", "Japanske yen"), "GB": ("British pound", "Britiske pund"),
             "CH": ("Swiss franc", "Sveitsiske franc"), "SE": ("Swedish krona", "Svenske kroner"), "DK": ("Danish krone", "Danske kroner"), "CA": ("Canadian dollar", "Canadiske dollar"),
             "AU": ("Australian dollar", "Australske dollar"), "NZ": ("New Zealand dollar", "New Zealandske dollar"), "NO": ("Norwegian krone", "Norske kroner"), "CN": ("Chinese yuan", "Kinesiske yuan"),
             "BR": ("Brazilian real", "Brasilianske real"), "IN": ("Indian rupee", "Indiske rupi"), "ID": ("Indonesian rupiah", "Indonesiske rupiah"), "KR": ("Korean won", "Sørkoreanske won"),
             "MX": ("Mexican peso", "Meksikanske peso"), "ZA": ("South African rand", "Sørafrikanske rand"), "TR": ("Turkish lira", "Tyrkiske lira"), "PL": ("Polish zloty", "Polske zloty")}
FUT_MONTHS = "FGHJKMNQUVXZ"
BIS_AREAS = {"US": ("Federal Reserve", "Federal Reserve", "Fed"), "XM": ("ECB", "ESB", "ECB"), "GB": ("Bank of England", "Bank of England", "BoE"),
             "JP": ("Bank of Japan", "Bank of Japan", "BoJ"), "NO": ("Norges Bank", "Norges Bank", "NB"), "SE": ("Riksbank", "Riksbanken", "Riksbank"),
             "CH": ("Swiss National Bank", "Sveitsiske nasjonalbanken", "SNB"), "AU": ("Reserve Bank of Australia", "Reserve Bank of Australia", "RBA"),
             "CA": ("Bank of Canada", "Bank of Canada", "BoC"), "DK": ("Danmarks Nationalbank", "Danmarks Nationalbank", "DN"),
             "NZ": ("Reserve Bank of New Zealand", "Reserve Bank of New Zealand", "RBNZ")}
BIS_TABLE = {"US": r"federal reserve", "XM": r"\becb\b", "GB": r"bank of england", "JP": r"bank of japan", "NO": r"norges", "SE": r"riksbank", "CH": r"swiss", "AU": r"australia", "CA": r"canada", "NZ": r"new zealand", "DK": r"danmarks|denmark"}
# the economies of the key-figures tab: OECD code -> (name_en, name_no, currency, BIS policy-rate area)
MACRO_AREAS = {"NOR": ("Norway", "Norge", "NOK", "NO"), "SWE": ("Sweden", "Sverige", "SEK", "SE"), "DNK": ("Denmark", "Danmark", "DKK", "DK"),
               "EA": ("Euro area", "Euroområdet", "EUR", "XM"), "DEU": ("Germany", "Tyskland", "EUR", None), "FRA": ("France", "Frankrike", "EUR", None),
               "GBR": ("United Kingdom", "Storbritannia", "GBP", "GB"), "CHE": ("Switzerland", "Sveits", "CHF", "CH"), "USA": ("United States", "USA", "USD", "US"),
               "CAN": ("Canada", "Canada", "CAD", "CA"), "JPN": ("Japan", "Japan", "JPY", "JP"), "AUS": ("Australia", "Australia", "AUD", "AU"),
               "NZL": ("New Zealand", "New Zealand", "NZD", "NZ"), "CHN": ("China", "Kina", "CNY", None)}
NB_SERIES = {"B.10Y.GBON": ("nok_10y", "Norway 10-year government bond"), "B.3Y.GBON": ("nok_3y", "Norway 3-year government bond"),
             "B.3M.TBIL": ("nok_3m", "Norway 3-month treasury bill"), "B.12M.TBIL": ("nok_12m", "Norway 12-month treasury bill")}


def log(msg):
    print(msg, flush=True)


def get(url, retries=3, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/csv,text/plain,*/*"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
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
def yahoo_chart(sym, rng="1y"):
    raw = cached(f"yahoo_{sym.replace('^', '_').replace('=', '_').replace('/', '_')}.json", 2,
                 lambda: get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sym)}?range={rng}&interval=1d"))
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
    # drop obviously bad prints (a value more than 3x or less than 1/3 of the one before it, e.g. a wrong opening tick)
    sane = lambda x, ref: ref is None or ref <= 0 or x is None or (ref / 3 < x < ref * 3)
    while len(vals) > 1 and not sane(vals[-1], vals[-2]):
        dates.pop(); vals.pop()
    # the latest print (may be an intraday value for a market that is open now)
    last = meta.get("regularMarketPrice")
    last_t = meta.get("regularMarketTime")
    if vals and not sane(last, vals[-1]):
        last, last_t = None, None
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
    elif kind == "diff":
        ch = lambda base: None if base is None else round(last - base, 3)          # units of the index
    else:
        ch = lambda base: pct(last, base)
    hi, lo = max(v[-252:]), min(v[-252:])
    return {"last": last, "last_date": last_date if (s.get("last") is None or s["last_time"][:10] <= last_date) else s["last_time"][:10],
            "prev": prev, "d1": ch(prev), "w1": ch(back(7)), "m1": ch(back(30)), "ytd": ch(ytd_base), "hi52": hi, "lo52": lo,
            "unit": "bp" if kind == "yield" else "pt" if kind == "diff" else "%"}


def instruments():
    with open(DATA / "instruments.csv", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def fetch_yahoo(previous):
    out, failed = {}, []
    for ins in instruments():
        sym = ins["symbol"]
        try:
            s = yahoo_chart(sym)
            # Yahoo sometimes returns a single point for a continuous futures contract around a roll (TTF=F, 12 Nov 2026):
            # splice the new prints onto the history kept from the previous run instead of losing the series for a day
            pv = previous.get(sym)
            if len(s["values"]) < 20 and pv and len(pv.get("dates", [])) >= 20:
                keep = [(d, v) for d, v in zip(pv["dates"], pv["values"]) if not s["dates"] or d < s["dates"][0]]
                s = {**s, "dates": [d for d, _ in keep] + s["dates"], "values": [v for _, v in keep] + s["values"]}
                log(f"  {sym}: Yahoo returned {len(s['dates']) - len(keep)} point(s); history kept from the previous run")
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
            kind = "diff" if sid == "NFCI" else "yield"
            out[sid] = {"name_en": label, "group": FRED_GROUP.get(sid, "credit" if "OAS" in label else "rates"), "kind": kind, "decimals": 2, "dates": s["dates"], "values": s["values"], **stats(s, kind)}
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
    for key, label in ECB_YC_ALL.items():
        raw = cached(f"ecb_all_{key}.csv", 6, lambda key=key: get(f"https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_C.SV_C_YM.{key}?format=csvdata&lastNObservations=280"))
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "ignore"))))
        obs = sorted((r["TIME_PERIOD"], float(r["OBS_VALUE"])) for r in rows if r.get("OBS_VALUE"))
        s = {"dates": [o[0] for o in obs], "values": [o[1] for o in obs]}
        out["EA_ALL_" + key.replace("SR_", "")] = {"name_en": label, "group": "rates", "kind": "yield", "decimals": 2, **s, **stats(s, "yield")}
    raw = cached("ecb_estr.csv", 6, lambda: get("https://data-api.ecb.europa.eu/service/data/EST/B.EU000A2X2A25.WT?format=csvdata&lastNObservations=280"))
    obs = sorted((r["TIME_PERIOD"], float(r["OBS_VALUE"])) for r in csv.DictReader(io.StringIO(raw.decode("utf-8", "ignore"))) if r.get("OBS_VALUE"))
    s = {"dates": [o[0] for o in obs], "values": [o[1] for o in obs]}
    out["ESTR"] = {"name_en": "€STR (euro overnight)", "group": "money", "kind": "yield", "decimals": 3, **s, **stats(s, "yield")}
    log(f"ecb: {len(out)} series, latest {max(v['last_date'] for v in out.values())}")
    return out


def fetch_gov():
    """Government yields beyond the OECD and FRED: Germany 2, 10 and 30 years (Bundesbank term structure of listed
    Federal securities), Japan 2, 10 and 30 years (Ministry of Finance JGB file), and SONIA, the sterling overnight rate
    (Bank of England). Each from its own open daily source."""
    out = {}
    start = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
    def put(key, name_en, pts, group="rates", decimals=2):
        pts = sorted((d, float(v)) for d, v in pts if v is not None and d >= start)
        if not pts:
            raise ValueError("no observations")
        s = {"dates": [q[0] for q in pts], "values": [round(q[1], 4) for q in pts]}
        out[key] = {"name_en": name_en, "group": group, "kind": "yield", "decimals": decimals, **s, **stats(s, "yield")}
    for code, key, name in (("R02XX", "DE_2Y", "Germany 2-year"), ("R10XX", "DE_10Y", "Germany 10-year"), ("R30XX", "DE_30Y", "Germany 30-year")):
        try:
            raw = cached(f"bbk_{code}.csv", 6, lambda code=code: get(f"https://api.statistiken.bundesbank.de/rest/data/BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.{code}.R.A.A._Z._Z.A?format=csv&startPeriod={start}"))
            pts = []
            for line in raw.decode("utf-8-sig", "ignore").splitlines():
                f = line.split(";")
                if len(f) >= 2 and len(f[0]) == 10 and f[0][4] == "-" and f[1] not in ("", "."):
                    pts.append((f[0], f[1].replace(",", ".")))
            put(key, name, pts)
        except Exception as e:
            log(f"  gov {key}: {repr(e)[:120]}")
    try:
        raw = cached("mof_jgb.csv", 6, lambda: get("https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv", timeout=90))
        cur = cached("mof_jgb_month.csv", 6, lambda: get("https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv", timeout=60))
        cols = {"JP_2Y": ("2Y", "Japan 2-year"), "JP_10Y": ("10Y", "Japan 10-year"), "JP_30Y": ("30Y", "Japan 30-year")}
        pts = {k: [] for k in cols}
        for r in csv.reader(io.StringIO(raw.decode("utf-8", "ignore") + chr(10) + cur.decode("utf-8", "ignore"))):
            if r and r[0] == "Date":
                idx = {k: r.index(c) for k, (c, _) in cols.items() if c in r}
                continue
            if len(r) > 1 and r[0].count("/") == 2:
                y, m, d = r[0].split("/")
                for k, j in idx.items():
                    if len(r) > j and r[j] not in ("", "-"):
                        pts[k].append((f"{y}-{int(m):02d}-{int(d):02d}", r[j]))
        for k, (_, name) in cols.items():
            put(k, name, pts[k], decimals=3)
    except Exception as e:
        log(f"  gov JP: {repr(e)[:120]}")
    try:
        raw = cached("boe_sonia.csv", 6, lambda: get("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom=01/Jan/2025&Dateto=now&SeriesCodes=IUDSOIA&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N", timeout=60))
        pts = []
        for r in csv.reader(io.StringIO(raw.decode("utf-8", "ignore"))):
            if len(r) >= 2 and r[0] != "DATE":
                try:
                    pts.append((datetime.strptime(r[0], "%d %b %Y").strftime("%Y-%m-%d"), r[1]))
                except ValueError:
                    pass
        put("SONIA", "SONIA (sterling overnight)", pts, group="money", decimals=3)
    except Exception as e:
        log(f"  gov SONIA: {repr(e)[:120]}")
    log(f"gov: {len(out)} series: " + ", ".join(f"{k} {v['last']} ({v['last_date']})" for k, v in out.items()))
    return out


def fetch_curves():
    """Futures curves for Brent and WTI from Yahoo's monthly contracts: the next 24 delivery months, each with six months
    of closes, so the page can draw today's curve against earlier ones. (TTF has only two listed months on Yahoo, so no
    gas curve.)"""
    out = {}
    now = datetime.now(timezone.utc)
    for root, name_en, name_no in (("BZ", "Brent", "Brent"), ("CL", "WTI", "WTI")):
        contracts = []
        y, m = now.year, now.month
        for i in range(26):
            mm = (m - 1 + i) % 12 + 1
            yy = y + (m - 1 + i) // 12
            sym = f"{root}{FUT_MONTHS[mm - 1]}{str(yy)[2:]}.NYM"
            try:
                sr = yahoo_chart(sym, "6mo")
                if len(sr["values"]) < 5:
                    continue
                contracts.append({"m": f"{yy}-{mm:02d}", "sym": sym, "dates": sr["dates"][-130:], "values": sr["values"][-130:]})
            except Exception:
                continue
        out[root] = {"name_en": name_en, "name_no": name_no, "unit": "USD/bbl", "contracts": contracts}
        log(f"curves {root}: {len(contracts)} contracts" + (f", {contracts[0]['m']} {contracts[0]['values'][-1]} … {contracts[-1]['m']} {contracts[-1]['values'][-1]}" if contracts else ""))
    return out


def fetch_eer():
    """BIS nominal effective exchange rates, broad basket, daily (2020 = 100; a rise is a stronger currency). The BIS
    publishes them about a week behind, so every row carries its date and the page marks the block as lagged."""
    raw = cached("bis_eer.csv", 12, lambda: get(f"https://stats.bis.org/api/v2/data/dataflow/BIS/WS_EER/1.0/D.N.B.{'+'.join(EER_AREAS)}?lastNObservations=420&format=csv", timeout=90))
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "ignore"))))
    out = {}
    num = lambda x: (lambda v: v if v == v else None)(float(x)) if x not in (None, "", "NaN", "nan") else None      # the BIS writes NaN on holidays
    for area, (en, no) in EER_AREAS.items():
        obs = sorted((r["TIME_PERIOD"], num(r.get("OBS_VALUE"))) for r in rows if r.get("REF_AREA") == area and num(r.get("OBS_VALUE")) is not None)
        if not obs:
            continue
        s = {"dates": [o[0] for o in obs], "values": [o[1] for o in obs]}
        out["EER_" + area] = {"name_en": en, "name_no": no, "group": "eer", "kind": "price", "decimals": 2, "lagged": True, **s, **stats(s, "price")}
    log(f"bis eer: {len(out)} currencies, latest {max(v['last_date'] for v in out.values()) if out else 'none'}")
    return out


def fetch_mpr():
    """The publication dates of Norges Bank's Monetary Policy Reports, from the bank's own list page, so the tables can
    show every change since the last report."""
    raw = cached("nb_mpr_list.html", 12, lambda: get("https://www.norges-bank.no/aktuelt/publikasjoner/Pengepolitisk-rapport/", timeout=60))
    html = raw.decode("utf-8", "ignore")
    dates = sorted(set(re.findall(r'Pengepolitisk-rapport/20\d\d/[^"]*"[^<]*>Pengepolitisk rapport \d/20\d\d</a>.*?dateTime="(20\d\d-\d\d-\d\d)', html, flags=re.S)))
    if not dates:
        raise ValueError("no report dates found")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    past = [d for d in dates if d <= today]
    log(f"mpr: {len(dates)} reports, last {past[-1] if past else 'none'}")
    return {"dates": dates, "last": past[-1] if past else None}


def derive(blocks):
    """Series built from others: the 5y5y forward nominal rates for the US and the euro area (2 x 10-year minus 5-year),
    and the euro-area periphery spread (all-issuer minus AAA 10-year, percentage points)."""
    out = {}
    def combine(key, a, b, fn, name_en, decimals=2):
        if not a or not b:
            return
        m = {d: v for d, v in zip(b["dates"], b["values"])}
        pts = [(d, round(fn(v, m[d]), 4)) for d, v in zip(a["dates"], a["values"]) if d in m]
        if len(pts) < 5:
            return
        s = {"dates": [q[0] for q in pts], "values": [q[1] for q in pts]}
        out[key] = {"name_en": name_en, "group": "rates", "kind": "yield", "decimals": decimals, **s, **stats(s, "yield")}
    fr, ec = blocks.get("fred", {}), blocks.get("ecb", {})
    combine("US_5Y5Y", fr.get("DGS10"), fr.get("DGS5"), lambda t, f: 2 * t - f, "US 5y5y forward")
    combine("EA_5Y5Y", ec.get("EA_SR_10Y"), ec.get("EA_SR_5Y"), lambda t, f: 2 * t - f, "Euro area AAA 5y5y forward")
    combine("EA_PERIPH", ec.get("EA_ALL_10Y"), ec.get("EA_SR_10Y"), lambda a, b: a - b, "Euro area 10-year: all issuers over AAA")
    log("derived: " + ", ".join(f"{k} {v['last']:.2f} ({v['last_date']})" for k, v in out.items()))
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


# FOMC decision days (second day of each meeting); the next two are priced from the Fed funds futures
FOMC = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
        "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-16", "2027-07-28", "2027-09-22", "2027-10-27", "2027-12-08"]
ZQ_MONTH = "FGHJKMNQUVXZ"


def fetch_fedfunds():
    """What the Fed funds futures price for the next two FOMC meetings, the CME FedWatch arithmetic: a contract settles on
    the month's average effective rate, so for a meeting on day d of an N-day month the average is
    (d-1)/N * rate_before + (N-d+1)/N * rate_after. rate_before for the first meeting is the current month's contract
    (no meeting earlier in it), and when the month after a meeting has no meeting its contract is rate_after directly.
    The probability of a 25 bp move is the priced change divided by 25 bp, capped at 100."""
    import calendar
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    nxt = [d for d in FOMC if d >= today][:2]
    if not nxt:
        return {}
    def contract(ym):
        y, m = int(ym[:4]), int(ym[5:7])
        sym = f"ZQ{ZQ_MONTH[m - 1]}{str(y)[2:]}.CBT"
        s = yahoo_chart(sym)
        price = s["last"] if s["last"] is not None else (s["values"][-1] if s["values"] else None)
        return sym, (None if price is None else round(100 - float(price), 4)), s["last_time"] or (s["dates"][-1] if s["dates"] else None)
    def month_has_meeting(ym):
        return any(d.startswith(ym) for d in FOMC)
    def next_month(ym):
        y, m = int(ym[:4]), int(ym[5:7]); m += 1
        return f"{y + (m > 12)}-{(m - 1) % 12 + 1:02d}"
    contracts, meetings = {}, []
    cur_sym, r_before, asof = contract(today[:7]); contracts[cur_sym] = r_before
    effr = r_before
    for d in nxt:
        ym, day = d[:7], int(d[8:10]); n = calendar.monthrange(int(ym[:4]), int(ym[5:7]))[1]
        sym, avg, t_ = contract(ym); contracts[sym] = avg
        after_ym = next_month(ym)
        if not month_has_meeting(after_ym):
            sym2, r_after, t2 = contract(after_ym); contracts[sym2] = r_after; used = sym2; asof = t2 or asof
        else:
            r_after = (n * avg - (day - 1) * r_before) / (n - day + 1) if avg is not None else None; used = sym; asof = t_ or asof
        if r_after is None:
            break
        chg = round(100 * (r_after - r_before), 1)
        meetings.append({"date": d, "rate_before": round(r_before, 3), "rate_after": round(r_after, 3), "change_bp": chg, "p_hike": min(100, round(max(0, chg) / 25 * 100)), "p_cut": min(100, round(max(0, -chg) / 25 * 100)), "contract": used})
        r_before = r_after
    out = {"effr_proxy": effr, "asof": asof, "contracts": contracts, "meetings": meetings}
    log(f"fed funds futures: " + "; ".join(f"{m['date']}: {m['change_bp']:+.1f} bp (hike {m['p_hike']} %, cut {m['p_cut']} %)" for m in meetings))
    return out


def fetch_bis():
    """Daily policy rates from the BIS (dataset WS_CBPOL) for the nine banks of the table, one year back, forward-filled
    over weekends and holidays. The BIS series run about a week behind, so each is extended to today with the rate in
    data/policy_rates.csv: when the table has a newer decision the step is put at its last_decision date. For the Fed
    the BIS gives the midpoint of the 25 bp target range; 12.5 bp is added so the series is the upper bound of the range,
    the figure usually quoted as the policy rate, and the table's range is read as its upper bound the same way."""
    import re
    start = (datetime.now(timezone.utc) - timedelta(days=380)).strftime("%Y-%m-%d")
    raw = cached("bis_cbpol.csv", 20, lambda: get(f"https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/D.{'+'.join(BIS_AREAS)}?startPeriod={start}&format=csv"))
    obs = {}
    for r in csv.DictReader(io.StringIO(raw.decode("utf-8", "ignore"))):
        try:
            v = float(r.get("OBS_VALUE") or "nan")
        except ValueError:
            continue
        if v == v and r.get("REF_AREA") in BIS_AREAS:
            obs.setdefault(r["REF_AREA"], {})[r["TIME_PERIOD"]] = v + 0.125 if r["REF_AREA"] == "US" else v
    table = {}
    for row in read_csv("policy_rates.csv"):
        nums = [float(x.replace(",", ".")) for x in re.findall(r"-?\d+(?:[.,]\d+)?", row.get("rate") or "")]
        if not nums:
            continue
        for area, rx in BIS_TABLE.items():
            if re.search(rx, row.get("bank") or "", re.I):
                table[area] = (max(nums), (row.get("last_decision") or "")[:10])
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = {}
    for area, (name_en, name_no, short) in BIS_AREAS.items():
        pts = sorted(obs.get(area, {}).items())
        if not pts:
            continue
        # forward-fill to a calendar-day series, then extend with the table
        d0, d1 = datetime.strptime(pts[0][0], "%Y-%m-%d"), datetime.strptime(pts[-1][0], "%Y-%m-%d")
        by = dict(pts); dates, values, cur = [], [], pts[0][1]
        n = (d1 - d0).days
        for i in range(n + 1):
            d = (d0 + timedelta(days=i)).strftime("%Y-%m-%d"); cur = by.get(d, cur); dates.append(d); values.append(cur)
        ext = None
        if area in table:
            rate, dec = table[area]
            if abs(rate - values[-1]) > 1e-9 and dec > dates[-1]:
                ext = dec
                # the old rate runs up to the day before the decision, then the new one from the decision day
                d = datetime.strptime(dec, "%Y-%m-%d")
                while dates[-1] < (d - timedelta(days=1)).strftime("%Y-%m-%d"):
                    dates.append((datetime.strptime(dates[-1], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")); values.append(values[-1])
                dates.append(dec); values.append(rate)
            elif abs(rate - values[-1]) > 1e-9:
                log(f"  bis {area}: table says {rate} from {dec}, BIS has {values[-1]} on {dates[-1]}; keeping BIS")
        if dates[-1] < today:
            dates.append(today); values.append(values[-1])
        changes = [(dates[i], values[i - 1], values[i]) for i in range(1, len(values)) if values[i] != values[i - 1]]
        out[area] = {"name_en": name_en, "name_no": name_no, "short": short, "group": "policy", "kind": "yield", "decimals": 2, "dates": dates, "values": values,
                     "last": values[-1], "last_date": dates[-1], "bis_last": pts[-1][0], "extended_from": ext, "changes": changes}
    log(f"bis: {len(out)} policy rates, BIS data to {max(v['bis_last'] for v in out.values())}; extended from the table: {[a for a, v in out.items() if v['extended_from']] or 'none'}")
    return out


OECD = "https://sdmx.oecd.org/public/rest/data/"


def oecd_rows(name, flow, key, params):
    raw = cached(name, 12, lambda: get(f"{OECD}{flow}/{key}?{params}&format=csvfilewithlabels", timeout=120))
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig", "ignore"))))


def fetch_macro():
    """Key figures for the economies of MACRO_AREAS: GDP growth (quarter on quarter and over a year, OECD quarterly national
    accounts), CPI and core CPI over a year (OECD consumer prices; the COICOP 1999 dataflow stops at the end of 2025 for most
    European countries and the COICOP 2018 dataflow carries on from 2026, so both are read and merged), the unemployment rate
    (OECD labour force statistics, monthly where available, otherwise quarterly; the euro area from Eurostat), and the
    3-month and 10-year interest rates (OECD financial indicators, monthly averages). Three years of history each. The
    12-month rates come from the national sources in fetch_r12m, which runs after this."""
    import re
    areas = "+".join(a for a in MACRO_AREAS if a != "EA") + "+EA+EA20"      # the euro area is EA in most dataflows, EA20 in the rates one
    alias = {"EA20": "EA", "EA21": "EA"}
    cand = {}       # (area, indicator) -> {variant: {date: value}}; a variant is the methodology/adjustment/frequency combination
    def add(area, ind, variant, period, value):
        area = alias.get(area, area)
        if area not in MACRO_AREAS:
            return
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        m = re.match(r"(\d{4})-Q(\d)$", period or "")
        date = f"{m.group(1)}-{int(m.group(2)) * 3:02d}-01" if m else (period + "-01" if len(period) == 7 else period[:10])
        cand.setdefault((area, ind), {}).setdefault(variant, {})[date] = v
    for tag, flow in (("c99", "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0"), ("c18", "OECD.SDD.TPS,DSD_PRICES_COICOP2018@DF_PRICES_C2018_ALL,1.0")):
        for r in oecd_rows(f"oecd_cpi_{tag}.csv", flow, f"{areas}.M..CPI.PA._T+_TXCP01_NRG..GY", "startPeriod=2023-01"):
            add(r["REF_AREA"], "cpi" if r["EXPENDITURE"] == "_T" else "core", (r["METHODOLOGY"], r["ADJUSTMENT"]), r["TIME_PERIOD"], r["OBS_VALUE"])
    # New Zealand's CPI is quarterly (Stats NZ); the OECD carries it at quarterly frequency only
    try:
        for r in oecd_rows("oecd_cpi_nzl_q.csv", "OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0", "NZL.Q..CPI.PA._T+_TXCP01_NRG..GY", "startPeriod=2023-Q1"):
            add(r["REF_AREA"], "cpi" if r["EXPENDITURE"] == "_T" else "core", (r["METHODOLOGY"], r["ADJUSTMENT"]), r["TIME_PERIOD"], r["OBS_VALUE"])
    except Exception as e:
        log(f"  oecd cpi NZL: {repr(e)[:120]}")
    for r in oecd_rows("oecd_unemp.csv", "OECD.SDD.TPS,DSD_LFS@DF_IALFS_UNE_M,1.0", f"{areas}.UNE_LF_M.PT_LF_SUB._Z.Y._T.Y_GE15..", "startPeriod=2023-01"):
        if r["FREQ"] in ("M", "Q"):
            add(r["REF_AREA"], "unemp", (r["FREQ"],), r["TIME_PERIOD"], r["OBS_VALUE"])
    for r in oecd_rows("oecd_gdp.csv", "OECD.SDD.NAD,DSD_NAMAIN1@DF_QNA,1.1", f"Q..{areas}...B1GQ......G1+GY.", "startPeriod=2023-Q1"):
        add(r["REF_AREA"], "gdp_qoq" if r["TRANSFORMATION"] == "G1" else "gdp_yoy", (r["ADJUSTMENT"], r["PRICE_BASE"]), r["TIME_PERIOD"], r["OBS_VALUE"])
    for r in oecd_rows("oecd_rates.csv", "OECD.SDD.STES,DSD_STES@DF_FINMARK,4.0", f"{areas}.M.IRLT+IR3TIB......", "startPeriod=2023-01"):
        add(r["REF_AREA"], "r10y" if r["MEASURE"] == "IRLT" else "r3m", (r["ADJUSTMENT"],), r["TIME_PERIOD"], r["OBS_VALUE"])
    try:
        j = json.loads(cached("eurostat_une_ea.json", 12, lambda: get("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/une_rt_m?geo=EA21&s_adj=SA&age=TOTAL&unit=PC_ACT&sex=T&sinceTimePeriod=2023-01&format=JSON")))
        for period, i in j["dimension"]["time"]["category"]["index"].items():
            if str(i) in j["value"]:
                add("EA", "unemp", ("M",), period, j["value"][str(i)])
    except Exception as e:
        log(f"  eurostat: {repr(e)[:120]}")
    # national CPI before the harmonised one, monthly before quarterly, seasonally adjusted chain-linked volumes for GDP
    pref = {"cpi": [("N", "N"), ("HICP", "N")], "core": [("N", "N"), ("HICP", "N")], "unemp": [("M",), ("Q",)],
            "gdp_qoq": [("Y", "LR"), ("Y", "L"), ("Y", "Q")], "gdp_yoy": [("Y", "LR"), ("Y", "L"), ("Y", "Q")], "r3m": [("_Z",), ("N",)], "r10y": [("_Z",), ("N",)]}
    series = {}
    for (area, ind), variants in cand.items():
        key = next((v for v in pref.get(ind, []) if v in variants), None) or max(variants, key=lambda v: max(variants[v]))
        pts = sorted(variants[key].items())
        freq = "Q" if ind.startswith("gdp") or (ind == "unemp" and key == ("Q",)) else "M"
        series.setdefault(area, {})[ind] = {"freq": freq, "dates": [p[0] for p in pts], "values": [round(p[1], 3) for p in pts], "last": round(pts[-1][1], 3), "last_date": pts[-1][0],
                                            "prev": round(pts[-2][1], 3) if len(pts) > 1 else None, "variant": "/".join(key)}
    out = {"areas": [{"code": a, "name_en": n[0], "name_no": n[1], "ccy": n[2], "bis": n[3]} for a, n in MACRO_AREAS.items()], "series": series,
           "sources": {"gdp": "OECD quarterly national accounts", "cpi": "OECD consumer prices", "unemp": "OECD labour force statistics; Eurostat for the euro area",
                       "rates": "OECD financial indicators, monthly averages"}}
    log(f"macro: {len(series)} economies, " + ", ".join(f"{k} {sum(1 for a in series.values() if k in a)}" for k in ("gdp_yoy", "cpi", "core", "unemp", "r3m", "r10y")))
    return out


def fetch_r12m(blocks):
    """The 12-month government rate per economy, daily, from each economy's own source, added to the macro block as the
    indicator r12m (the OECD has no 12-month rate): Norway the 12-month treasury bill (Norges Bank), the euro area the
    AAA 1-year spot rate (ECB), the United States the 1-year constant maturity (FRED), Germany the 1-year Bund term
    structure (Bundesbank), Japan the 1-year JGB (Ministry of Finance), Canada the 1-year treasury bill mid yield (Bank
    of Canada). No open daily source was found for Sweden (the Riksbank's 12-month bill series closed in 2010), Denmark,
    the United Kingdom, Switzerland, France, Australia or China; those economies have no 12-month rate on the page."""
    series = blocks.get("macro", {}).get("series")
    if series is None:
        return
    start = (datetime.now(timezone.utc) - timedelta(days=3 * 366)).strftime("%Y-%m-%d")
    sources = {}
    def put(area, pts, src):
        pts = sorted((d, float(v)) for d, v in pts if v is not None and d >= start)
        if not pts:
            raise ValueError("no observations")
        series.setdefault(area, {})["r12m"] = {"freq": "D", "dates": [p[0] for p in pts], "values": [round(p[1], 3) for p in pts], "last": round(pts[-1][1], 3),
                                               "last_date": pts[-1][0], "prev": round(pts[-2][1], 3) if len(pts) > 1 else None, "variant": src}
        sources[area] = src
    def from_block(area, block, key, src):
        v = blocks.get(block, {}).get(key)
        if v:
            put(area, zip(v["dates"], v["values"]), src)
    def bundesbank():
        raw = cached("bbk_1y.csv", 6, lambda: get("https://api.statistiken.bundesbank.de/rest/data/BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604.R01XX.R.A.A._Z._Z.A?format=csv&startPeriod=" + start))
        pts = []
        for line in raw.decode("utf-8-sig", "ignore").splitlines():
            f = line.split(";")
            if len(f) >= 2 and len(f[0]) == 10 and f[0][4] == "-" and f[1] not in ("", "."):
                pts.append((f[0], f[1].replace(",", ".")))
        put("DEU", pts, "Deutsche Bundesbank, 1-year term structure of listed Federal securities")
    def mof():
        raw = cached("mof_jgb.csv", 6, lambda: get("https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv", timeout=90))
        # the historical file lags by a few weeks; the current month's file carries the latest days
        cur = cached("mof_jgb_month.csv", 6, lambda: get("https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv", timeout=60))
        pts = []
        for r in csv.reader(io.StringIO(raw.decode("utf-8", "ignore") + chr(10) + cur.decode("utf-8", "ignore"))):
            if len(r) > 1 and r[0].count("/") == 2 and r[1] not in ("", "-"):
                y, m, d = r[0].split("/")
                pts.append((f"{y}-{int(m):02d}-{int(d):02d}", r[1]))
        put("JPN", pts, "Ministry of Finance, 1-year JGB")
    def boc():
        j = json.loads(cached("boc_1y.json", 6, lambda: get(f"https://www.bankofcanada.ca/valet/observations/TB.CDN.1Y.MID/json?start_date={start}")))
        put("CAN", [(o["d"], o["TB.CDN.1Y.MID"]["v"]) for o in j["observations"] if o.get("TB.CDN.1Y.MID", {}).get("v")], "Bank of Canada, 1-year treasury bill mid-market yield")
    for area, fn in (("NOR", lambda: from_block("NOR", "nb", "nok_12m", "Norges Bank, 12-month treasury bill")), ("EA", lambda: from_block("EA", "ecb", "EA_SR_1Y", "ECB, AAA 1-year spot rate")),
                     ("USA", lambda: from_block("USA", "fred", "DGS1", "FRED, 1-year Treasury constant maturity")), ("DEU", bundesbank), ("JPN", mof), ("CAN", boc)):
        try:
            fn()
        except Exception as e:
            log(f"  r12m {area}: {repr(e)[:120]}")
    blocks["macro"]["sources"]["r12m"] = sources
    log("r12m: " + ", ".join(f"{a} {series[a]['r12m']['last']} ({series[a]['r12m']['last_date']})" for a in series if "r12m" in series[a]))


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
def read_json(name, default):
    p = DATA / name
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except Exception as e:
        log(f"  !! {name}: {repr(e)[:120]}")
        return default


def read_csv(name):
    p = DATA / name
    if not p.exists():
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def fetch_heatmap(n=150):
    """The largest US-listed stocks by market cap with the day's change (Yahoo's predefined large-cap screener, which
    needs no login). Used for the treemap on the Equities tab. One share class per company (GOOGL, BRK-B)."""
    raw = cached("yahoo_screener_large_caps.json", 2, lambda: get(
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved?scrIds=large_cap_stocks&count=250"))
    quotes = json.loads(raw)["finance"]["result"][0]["quotes"]
    quotes = [q for q in quotes if q.get("marketCap") and q.get("regularMarketChangePercent") is not None and q.get("quoteType") == "EQUITY"]
    quotes.sort(key=lambda q: -q["marketCap"])
    seen, items = set(), []
    for q in quotes:
        name = (q.get("longName") or q.get("shortName") or q["symbol"]).strip()
        key = name.lower().replace(" new", "").rstrip(".")   # GOOG/GOOGL, BRK-A/BRK-B: keep the larger listing only
        if key in seen:
            continue
        seen.add(key)
        t = q.get("regularMarketTime")
        items.append({"s": q["symbol"], "n": name, "cap": q["marketCap"], "chg": round(q["regularMarketChangePercent"], 3),
                      "t": datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if t else None})
        if len(items) >= n:
            break
    if len(items) < 20:
        raise ValueError(f"only {len(items)} stocks from the screener")
    log(f"heatmap: {len(items)} stocks, latest print {max(i['t'] or '' for i in items)}")
    return {"fetched": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "items": items}


def xlsx_rows(raw, sheet_name):
    """The rows of one sheet of an .xlsx as lists of values (float, str or None), without openpyxl: dates come back as
    Excel serial numbers (floats), strings through the shared-string table."""
    import xml.etree.ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "p": "http://schemas.openxmlformats.org/package/2006/relationships"}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rid = next(s.get("{%s}id" % ns["r"]) for s in wb.find("m:sheets", ns) if s.get("name") == sheet_name)
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels if r.get("Id") == rid).lstrip("/")
        path = target if target.startswith("xl/") else "xl/" + target
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = ["".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])) for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
        rows = []
        for row in ET.fromstring(z.read(path)).iter("{%s}row" % ns["m"]):
            vals = []
            for c in row.findall("m:c", ns):
                v = c.find("m:v", ns)
                if v is None or v.text is None:
                    vals.append(None)
                elif c.get("t") == "s":
                    vals.append(shared[int(v.text)])
                elif c.get("t") in ("str", "inlineStr"):
                    vals.append(v.text)
                else:
                    vals.append(float(v.text))
            rows.append(vals)
    return rows


def excel_date(serial):
    return (datetime(1899, 12, 30) + timedelta(days=float(serial))).strftime("%Y-%m-%d")


def fetch_extra_commodities(yahoo, previous):
    """Two reference prices that no futures contract gives, added to the instrument block next to the Yahoo futures:
    Dated Brent, the physical spot price that the oil companies' krone purchases follow (EIA's Europe Brent spot FOB
    through FRED, series DCOILBRENTEU; the EIA updates it weekly, so it runs a few days behind the front contract), and
    the UK gas price at the NBP, which prices about a quarter of Norwegian gas exports, as National Gas's daily System
    Average Price of gas traded on the on-the-day market (the ONS real-time indicators spreadsheet, weekly, p/kWh here
    converted to pence per therm at 29.3071 kWh per therm)."""
    start = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d")
    def add(sym, obs, name_en, name_no, currency, decimals):
        obs = sorted(set((d, float(v)) for d, v in obs if v is not None and d >= start))
        if len(obs) < 10:
            raise ValueError(f"only {len(obs)} observations")
        s = {"dates": [o[0] for o in obs], "values": [round(o[1], 3) for o in obs]}
        yahoo[sym] = {"group": "commodity", "name_en": name_en, "name_no": name_no, "kind": "price", "decimals": decimals, "currency": currency, "last_time": None, **s, **stats(s, "price")}
        log(f"  {sym}: {yahoo[sym]['last']} ({yahoo[sym]['last_date']})")
    for sym, fn in (("BRENT_DATED", lambda: add("BRENT_DATED", parse_csv_tables(cached("fred_brent.csv", 6, lambda: get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU&cosd={start}"))).get("DCOILBRENTEU", []),
                                                "Dated Brent spot (USD/bbl)", "Dated Brent spot (USD/fat)", "USD", 2)),
                    ("UK_SAP", lambda: add("UK_SAP", uk_sap_rows(), "UK gas, NBP system average price (p/therm)", "Britisk gass, NBP systemgjennomsnittspris (pence/therm)", "GBp", 1))):
        try:
            fn()
        except Exception as e:
            log(f"  !! {sym}: {repr(e)[:160]}")
            if sym in previous:
                yahoo[sym] = previous[sym]


# crosses Yahoo quotes without a history (NZD/NOK returns a single print): built from two quoted legs, date by date
CROSSES = {"NZDNOK=X": ("NZDUSD=X", "USDNOK=X")}


def derive_crosses(yahoo):
    for sym, (a, b) in CROSSES.items():
        cur = yahoo.get(sym)
        if cur and len(cur.get("dates", [])) >= 10:
            continue
        x, y = yahoo.get(a), yahoo.get(b)
        if not x or not y:
            continue
        ya = dict(zip(y["dates"], y["values"]))
        pts = [(d, round(v * ya[d], 5)) for d, v in zip(x["dates"], x["values"]) if d in ya]
        if len(pts) < 10:
            continue
        s = {"dates": [q[0] for q in pts], "values": [q[1] for q in pts]}
        if x.get("last") is not None and y.get("last") is not None and x.get("last_time") and y.get("last_time"):
            s["last"] = round(float(x["last"]) * float(y["last"]), 5); s["last_time"] = min(x["last_time"], y["last_time"])
        base = cur or {"group": "nok", "name_en": sym[:3] + "/" + sym[3:6], "name_no": sym[:3] + "/" + sym[3:6], "kind": "fx", "decimals": "4"}
        yahoo[sym] = {**{k: base[k] for k in ("group", "name_en", "name_no", "kind", "decimals")}, "currency": sym[3:6], "last_time": s.get("last_time"), "dates": s["dates"][-260:], "values": s["values"][-260:], **stats(s, "fx"), "derived": f"{a} x {b}"}
        log(f"  {sym}: derived from {a} x {b}, {len(pts)} days, last {yahoo[sym]['last']}")


def uk_sap_rows():
    """(date, pence per therm) from the latest ONS 'System Average Price (SAP) of gas' spreadsheet (daily table)."""
    page = cached("ons_sap.html", 6, lambda: get("https://www.ons.gov.uk/economy/economicoutputandproductivity/output/datasets/systemaveragepricesapofgas"))
    m = re.search(r'href="(/file\?uri=[^"]*systemaveragepriceofgasdataset[^"]*\.xlsx)"', page.decode("utf-8", "ignore"))
    if not m:
        raise ValueError("no spreadsheet link on the ONS page")
    raw = cached("ons_sap.xlsx", 6, lambda: get("https://www.ons.gov.uk" + m.group(1).replace("&amp;", "&")))
    out = []
    for r in xlsx_rows(raw, "1.Daily SAP Gas"):
        if len(r) >= 2 and isinstance(r[0], float) and isinstance(r[1], float) and r[0] > 40000:
            out.append((excel_date(r[0]), r[1] * 29.3071))
    return out


def fetch_pce(blocks):
    """US PCE and core PCE inflation over a year (BEA through FRED, PCEPI and PCEPILFE), added to the macro block of the
    United States as the indicators pce and pce_core; the key-figures table shows them under the CPI figures."""
    series = blocks.get("macro", {}).get("series")
    if series is None:
        return
    start = (datetime.now(timezone.utc) - timedelta(days=4 * 366)).strftime("%Y-%m-%d")
    tables = parse_csv_tables(cached("fred_pce.csv", 6, lambda: get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=PCEPI,PCEPILFE&cosd={start}")))
    for sid, ind in (("PCEPI", "pce"), ("PCEPILFE", "pce_core")):
        obs = tables.get(sid, [])
        idx = {d[:7]: v for d, v in obs}
        pts = []
        for d, v in obs:
            y, mth = int(d[:4]), int(d[5:7])
            base = idx.get(f"{y - 1:04d}-{mth:02d}")
            if base:
                pts.append((d, round(100 * (v / base - 1), 3)))
        pts = pts[-37:]
        if pts:
            series.setdefault("USA", {})[ind] = {"freq": "M", "dates": [p[0] for p in pts], "values": [p[1] for p in pts], "last": pts[-1][1], "last_date": pts[-1][0],
                                                 "prev": pts[-2][1] if len(pts) > 1 else None, "variant": "BEA via FRED, " + sid}
    blocks["macro"]["sources"]["pce"] = "BEA personal consumption expenditures price indices via FRED (PCEPI, PCEPILFE), 12-month change"
    log("pce: " + ", ".join(f"{k} {series['USA'][k]['last']} ({series['USA'][k]['last_date'][:7]})" for k in ("pce", "pce_core") if k in series.get("USA", {})))


def main():
    prev = json.loads((DATA / "data.json").read_text(encoding="utf-8")) if (DATA / "data.json").exists() else {}
    kept = []
    log("1. Yahoo")
    yahoo, failed = fetch_yahoo(prev.get("yahoo", {}))
    kept += [f"yahoo:{s}" for s in failed if s in yahoo]
    log("1b. Dated Brent and UK gas")
    fetch_extra_commodities(yahoo, prev.get("yahoo", {}))
    derive_crosses(yahoo)
    blocks = {}
    for name, fn in (("fred", fetch_fred), ("ecb", fetch_ecb), ("nb", fetch_nb), ("ssb", fetch_ssb), ("bis", fetch_bis), ("macro", fetch_macro), ("fedfunds", fetch_fedfunds), ("gov", fetch_gov), ("curves", fetch_curves), ("eer", fetch_eer), ("mpr", fetch_mpr), ("news", fetch_news), ("heatmap", fetch_heatmap)):
        log(f"{name}")
        try:
            blocks[name] = fn()
        except Exception as e:
            log(f"  !! {name} failed: {repr(e)[:200]}; keeping the previous data")
            blocks[name] = prev.get(name, {})
            kept.append(name)
    try:
        fetch_r12m(blocks)
    except Exception as e:
        log(f"  !! r12m failed: {repr(e)[:200]}")
    try:
        fetch_pce(blocks)
    except Exception as e:
        log(f"  !! pce failed: {repr(e)[:200]}")
    try:
        blocks["derived"] = derive(blocks)
    except Exception as e:
        log(f"  !! derived failed: {repr(e)[:200]}")
        blocks["derived"] = prev.get("derived", {})
    data = {"asof": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "yahoo": yahoo, **blocks,
            "policy_rates": read_csv("policy_rates.csv"), "events": read_csv("events.csv"), "cpi_consensus": read_csv("cpi_consensus.csv"), "cb_minutes": read_json("cb_minutes.json", {}), "kept_from_previous": kept}
    (DATA / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"wrote data/data.json ({(DATA / 'data.json').stat().st_size // 1024} kB); kept from previous: {kept or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
