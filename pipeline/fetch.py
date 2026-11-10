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
BIS_AREAS = {"US": ("Federal Reserve", "Federal Reserve", "Fed"), "XM": ("ECB", "ESB", "ECB"), "GB": ("Bank of England", "Bank of England", "BoE"),
             "JP": ("Bank of Japan", "Bank of Japan", "BoJ"), "NO": ("Norges Bank", "Norges Bank", "NB"), "SE": ("Riksbank", "Riksbanken", "Riksbank"),
             "CH": ("Swiss National Bank", "Sveitsiske nasjonalbanken", "SNB"), "AU": ("Reserve Bank of Australia", "Reserve Bank of Australia", "RBA"),
             "CA": ("Bank of Canada", "Bank of Canada", "BoC"), "DK": ("Danmarks Nationalbank", "Danmarks Nationalbank", "DN")}
BIS_TABLE = {"US": r"federal reserve", "XM": r"\becb\b", "GB": r"bank of england", "JP": r"bank of japan", "NO": r"norges", "SE": r"riksbank", "CH": r"swiss", "AU": r"australia", "CA": r"canada", "DK": r"danmarks|denmark"}
# the economies of the key-figures tab: OECD code -> (name_en, name_no, currency, BIS policy-rate area)
MACRO_AREAS = {"NOR": ("Norway", "Norge", "NOK", "NO"), "SWE": ("Sweden", "Sverige", "SEK", "SE"), "DNK": ("Denmark", "Danmark", "DKK", "DK"),
               "EA": ("Euro area", "Euroområdet", "EUR", "XM"), "DEU": ("Germany", "Tyskland", "EUR", None), "FRA": ("France", "Frankrike", "EUR", None),
               "GBR": ("United Kingdom", "Storbritannia", "GBP", "GB"), "CHE": ("Switzerland", "Sveits", "CHF", "CH"), "USA": ("United States", "USA", "USD", "US"),
               "CAN": ("Canada", "Canada", "CAD", "CA"), "JPN": ("Japan", "Japan", "JPY", "JP"), "AUS": ("Australia", "Australia", "AUD", "AU"), "CHN": ("China", "Kina", "CNY", None)}
NB_SERIES = {"B.10Y.GBON": ("nok_10y", "Norway 10-year government bond"), "B.3Y.GBON": ("nok_3y", "Norway 3-year government bond"),
             "B.3M.TBIL": ("nok_3m", "Norway 3-month treasury bill")}


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
    3-month and 10-year interest rates (OECD financial indicators, monthly averages). Three years of history each."""
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


def main():
    prev = json.loads((DATA / "data.json").read_text(encoding="utf-8")) if (DATA / "data.json").exists() else {}
    kept = []
    log("1. Yahoo")
    yahoo, failed = fetch_yahoo(prev.get("yahoo", {}))
    kept += [f"yahoo:{s}" for s in failed if s in yahoo]
    blocks = {}
    for name, fn in (("fred", fetch_fred), ("ecb", fetch_ecb), ("nb", fetch_nb), ("ssb", fetch_ssb), ("bis", fetch_bis), ("macro", fetch_macro), ("fedfunds", fetch_fedfunds), ("news", fetch_news), ("heatmap", fetch_heatmap)):
        log(f"{name}")
        try:
            blocks[name] = fn()
        except Exception as e:
            log(f"  !! {name} failed: {repr(e)[:200]}; keeping the previous data")
            blocks[name] = prev.get(name, {})
            kept.append(name)
    data = {"asof": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "yahoo": yahoo, **blocks,
            "policy_rates": read_csv("policy_rates.csv"), "events": read_csv("events.csv"), "cpi_consensus": read_csv("cpi_consensus.csv"), "cb_minutes": read_json("cb_minutes.json", {}), "kept_from_previous": kept}
    (DATA / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"wrote data/data.json ({(DATA / 'data.json').stat().st_size // 1024} kB); kept from previous: {kept or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
