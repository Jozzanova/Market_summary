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
INTRADAY = "--intraday" in sys.argv     # prices only, in a few requests; the daily blocks stay as the morning run left them
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


YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
_yahoo_refused = 0
YAHOO_DOWN = False


def yahoo_get(url):
    """Yahoo with a circuit breaker. The cloud runs share their egress addresses, and Yahoo answers 429 to all of them for a
    while (12 Nov 2026, 15:40 run: every symbol refused, fetch.py never finished within the routine's time budget). After four
    symbols in a row have been refused on both hosts, the rest of the run skips Yahoo and keeps the previous data instead of
    retrying eighty symbols one by one; data.json is still written and the brief says which prices are from the previous run."""
    global _yahoo_refused, YAHOO_DOWN
    if YAHOO_DOWN:
        raise RuntimeError("Yahoo rate-limited (429); skipped")
    last = None
    for host in YAHOO_HOSTS:
        try:
            raw = get(url.replace(YAHOO_HOSTS[0], host), retries=1 if host == YAHOO_HOSTS[0] else 0, timeout=30)
            _yahoo_refused = 0
            return raw
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 429:
                raise
    _yahoo_refused += 1
    if _yahoo_refused >= 4:
        YAHOO_DOWN = True
        log("  !! Yahoo refused four symbols in a row (429 on both hosts); Yahoo skipped for the rest of this run, previous data kept")
    raise last


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
                 lambda: yahoo_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(sym)}?range={rng}&interval=1d"))
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
    # the last daily close is taken as the "latest" value; if Yahoo reports a later live print, that is used instead, and
    # the day-, week- and month-ago references are counted from the print's date (not from the last close's, which put
    # the week-ago reference a day early)
    live = s.get("last") is not None and bool(s.get("last_time")) and s["last_time"][:10] > last_date
    if live:
        last, last_date = float(s["last"]), s["last_time"][:10]
    prev = v[-1] if live else v[-2]
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
    return {"last": last, "last_date": last_date,
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
# Reserve sources: CNBC's quote service and TradingView's scanner, both without a key and many symbols per request.
# Used only when Yahoo refuses (429 on the cloud address, 12 Nov 2026). Delayed up to 15 minutes; a print is spliced onto
# the history kept from the previous run, so the daily changes stay comparable. ^IRX stays out (Yahoo quotes the discount
# rate, CNBC the yield). Symbols without a map, or whose print fails the sanity check, keep the previous run's data.
# ----------------------------------------------------------------------------------------------------------------------
CNBC_SYM = {"^GSPC": ".SPX", "^IXIC": ".IXIC", "^DJI": ".DJI", "^RUT": ".RUT", "^STOXX": ".STOXX", "^STOXX50E": ".STOXX50E", "^GDAXI": ".GDAXI",
            "^FTSE": ".FTSE", "^FCHI": ".FCHI", "OSEBX.OL": ".OSEBX", "^OMX": ".OMXS30", "^N225": ".N225", "^HSI": ".HSI", "000001.SS": ".SSEC",
            "^KS11": ".KS11", "EEM": "EEM", "URTH": "URTH", "EXX1.DE": "EXX1-DE", "^VIX": ".VIX", "^SOX": ".SOX", "^NYFANG": ".NYFANG", "SOXX": "SOXX",
            "SMH": "SMH", "MAGS": "MAGS", "AIQ": "AIQ", "BOTZ": "BOTZ", "ROBT": "ROBT", "XLK": "XLK", "XLC": "XLC", "XLY": "XLY", "XLP": "XLP",
            "XLV": "XLV", "XLF": "XLF", "XLI": "XLI", "XLB": "XLB", "XLE": "XLE", "XLU": "XLU", "XLRE": "XLRE", "^FVX": "US5Y", "^TNX": "US10Y",
            "^TYX": "US30Y", "ZN=F": "@TY.1", "ZB=F": "@US.1", "^SPGSCI": ".SPGSCI", "BZ=F": "@LCO.1", "CL=F": "@CL.1", "NG=F": "@NG.1",
            "GC=F": "@GC.1", "SI=F": "@SI.1", "HG=F": "@HG.1", "PL=F": "@PL.1", "ZW=F": "@W.1", "ZC=F": "@C.1", "ZS=F": "@S.1", "DX-Y.NYB": ".DXY",
            "EURUSD=X": "EUR=", "GBPUSD=X": "GBP=", "USDJPY=X": "JPY=", "USDCHF=X": "CHF=", "USDCAD=X": "CAD=", "USDSEK=X": "SEK=",
            "USDCNY=X": "CNY=", "AUDUSD=X": "AUD=", "NZDUSD=X": "NZD=", "EURNOK=X": "EURNOK=", "USDNOK=X": "NOK=", "EURSEK=X": "EURSEK=",
            "BTC-USD": "BTC.CB=", "GBPNOK=X": "GBPNOK=", "SEKNOK=X": "SEKNOK=", "CHFNOK=X": "CHFNOK=", "JPYNOK=X": "JPYNOK=", "CADNOK=X": "CADNOK=",
            "AUDNOK=X": "AUDNOK=", "SPY": "SPY", "QQQ": "QQQ", "DIA": "DIA", "IWM": "IWM"}
CNBC_SCALE = {"JPYNOK=X": 0.01}     # CNBC quotes the krone per 100 yen, Yahoo per yen
TV_SYM = {"^GSPC": "SP:SPX", "^IXIC": "NASDAQ:IXIC", "^DJI": "DJ:DJI", "^RUT": "TVC:RUT", "^STOXX": "TVC:SXXP", "^STOXX50E": "TVC:SX5E",
          "^GDAXI": "XETR:DAX", "^FTSE": "TVC:UKX", "^FCHI": "TVC:CAC40", "OSEBX.OL": "OSL:OSEBX", "^OMX": "OMXSTO:OMXS30", "^N225": "TVC:NI225",
          "^HSI": "HSI:HSI", "000001.SS": "SSE:000001", "^KS11": "KRX:KOSPI", "EEM": "AMEX:EEM", "URTH": "AMEX:URTH", "EXX1.DE": "XETR:EXX1",
          "^VIX": "TVC:VIX", "^SOX": "NASDAQ:SOX", "^NYFANG": "ICEUS:NYFANG", "SOXX": "NASDAQ:SOXX", "SMH": "NASDAQ:SMH", "MAGS": "CBOE:MAGS", "AIQ": "NASDAQ:AIQ",
          "BOTZ": "NASDAQ:BOTZ", "ROBT": "NASDAQ:ROBT", "XLK": "AMEX:XLK", "XLC": "AMEX:XLC", "XLY": "AMEX:XLY", "XLP": "AMEX:XLP", "XLV": "AMEX:XLV",
          "XLF": "AMEX:XLF", "XLI": "AMEX:XLI", "XLB": "AMEX:XLB", "XLE": "AMEX:XLE", "XLU": "AMEX:XLU", "XLRE": "AMEX:XLRE", "^FVX": "TVC:US05Y",
          "^TNX": "TVC:US10Y", "^TYX": "TVC:US30Y", "ZN=F": "CBOT:ZN1!", "ZB=F": "CBOT:ZB1!", "^SPGSCI": "SP:SPGSCI", "BZ=F": "ICEEUR:BRN1!",
          "CL=F": "NYMEX:CL1!", "NG=F": "NYMEX:NG1!", "TTF=F": "ICEENDEX:TFM1!", "GC=F": "COMEX:GC1!", "SI=F": "COMEX:SI1!", "HG=F": "COMEX:HG1!",
          "ALI=F": "COMEX:ALI1!", "PL=F": "NYMEX:PL1!", "ZW=F": "CBOT:ZW1!", "ZC=F": "CBOT:ZC1!", "ZS=F": "CBOT:ZS1!", "DX-Y.NYB": "TVC:DXY",
          "EURUSD=X": "FX:EURUSD", "GBPUSD=X": "FX:GBPUSD", "USDJPY=X": "FX:USDJPY", "USDCHF=X": "FX:USDCHF", "USDCAD=X": "FX:USDCAD",
          "USDSEK=X": "FX:USDSEK", "AUDUSD=X": "FX:AUDUSD", "NZDUSD=X": "FX:NZDUSD", "EURNOK=X": "FX:EURNOK", "USDNOK=X": "FX:USDNOK",
          "EURSEK=X": "FX:EURSEK", "BTC-USD": "BITSTAMP:BTCUSD", "DKKNOK=X": "FX_IDC:DKKNOK", "NZDNOK=X": "FX_IDC:NZDNOK", "GBPNOK=X": "FX_IDC:GBPNOK",
          "SEKNOK=X": "FX_IDC:SEKNOK", "CHFNOK=X": "FX_IDC:CHFNOK", "JPYNOK=X": "FX_IDC:JPYNOK", "CADNOK=X": "FX_IDC:CADNOK", "AUDNOK=X": "FX_IDC:AUDNOK",
          "USDCNY=X": "FX_IDC:USDCNY", "ES=F": "CME_MINI:ES1!", "NQ=F": "CME_MINI:NQ1!", "YM=F": "CBOT_MINI:YM1!", "RTY=F": "CME_MINI:RTY1!",
          "SPY": "AMEX:SPY", "QQQ": "NASDAQ:QQQ", "DIA": "AMEX:DIA", "IWM": "AMEX:IWM"}


def _num(x):
    if x is None:
        return None
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def _cnbc_time(t):
    """CNBC's '2026-11-12T16:20:00.000-0500' (or a bare date) -> (local session date, 'YYYY-MM-DD HH:MM UTC' or None)."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?([+-])(\d{2})(\d{2}))?", str(t or ""))
    if not m:
        return None, None
    if not m.group(2):
        return m.group(1), None
    local = datetime.strptime(m.group(1) + " " + m.group(2) + ":" + m.group(3), "%Y-%m-%d %H:%M")
    off = timedelta(hours=int(m.group(6)), minutes=int(m.group(7))) * (1 if m.group(5) == "+" else -1)
    return m.group(1), (local - off).strftime("%Y-%m-%d %H:%M UTC")


def cnbc_quotes(syms):
    """{yahoo symbol: {last, prev, date, time, ext, ext_time, ext_type}} from CNBC's quote service, 40 symbols a request."""
    out = {}
    syms = [s for s in syms if s in CNBC_SYM]
    for ci in range(0, len(syms), 40):
        chunk = syms[ci:ci + 40]
        url = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=" + urllib.request.quote("|".join(CNBC_SYM[s] for s in chunk), safe="@.=-")
               + "&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json")
        qs = json.loads(get(url, retries=1, timeout=30)).get("FormattedQuoteResult", {}).get("FormattedQuote", [])
        back = {CNBC_SYM[s]: s for s in chunk}
        for q in qs:
            sym = back.get(q.get("symbol"))
            last = _num(q.get("last"))
            if not sym or last is None:
                continue
            k = CNBC_SCALE.get(sym, 1)
            date, when = _cnbc_time(q.get("last_time"))
            ext = q.get("ExtendedMktQuote") if isinstance(q.get("ExtendedMktQuote"), dict) else {}
            prev = _num(q.get("previous_day_closing"))
            e = {"last": last * k, "prev": prev * k if prev else None, "date": date, "time": when, "source": "CNBC"}
            if _num(ext.get("last")) is not None and ext.get("type") in ("PRE_MKT", "POST_MKT"):
                e.update(ext=_num(ext["last"]) * k, ext_time=_cnbc_time(ext.get("last_time"))[1], ext_type=ext["type"])
            out[sym] = e
    return out


def tradingview_quotes(syms):
    """{yahoo symbol: {last, prev, date, time}} from TradingView's scanner (one POST; the time is the session's start)."""
    syms = [s for s in syms if s in TV_SYM]
    if not syms:
        return {}
    body = json.dumps({"symbols": {"tickers": [TV_SYM[s] for s in syms], "query": {"types": []}},
                       "columns": ["close", "change", "time", "premarket_close", "premarket_change", "postmarket_close", "postmarket_change"], "range": [0, 500]}).encode()
    req = urllib.request.Request("https://scanner.tradingview.com/global/scan", data=body, headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = json.loads(r.read()).get("data", [])
    back = {TV_SYM[s]: s for s in syms}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = {}
    for row in rows:
        sym, d = back.get(row.get("s")), row.get("d") or []
        if not sym or len(d) < 3 or d[0] is None:
            continue
        close, chg, t = float(d[0]), d[1], d[2]
        ext = {k: d[i] for k, i in (("pre", 3), ("pre_chg", 4), ("post", 5), ("post_chg", 6)) if len(d) > i and d[i] is not None}
        out[sym] = {**ext, "last": close, "prev": round(close / (1 + chg / 100), 6) if chg is not None and chg > -100 else None,
                    # the bar's time is the session's start (FX and futures start the evening before, UTC): seven hours on gives the session date
                    "date": (datetime.fromtimestamp(t, tz=timezone.utc) + timedelta(hours=7)).strftime("%Y-%m-%d") if t else now[:10], "time": now, "source": "TradingView"}
    return out


def reserve_quotes(syms):
    """CNBC first, TradingView for what is missing or if CNBC fails; never raises."""
    out = {}
    for fn in (cnbc_quotes, tradingview_quotes):
        want = [s for s in syms if s not in out]
        if not want:
            break
        try:
            out.update(fn(want))
        except Exception as e:
            log(f"  !! reserve source {fn.__name__}: {repr(e)[:120]}")
    return out


def apply_reserve(out, failed, previous):
    """For instruments Yahoo refused: a reserve print spliced onto the previous run's series. Returns the symbols filled."""
    want = [s for s in failed if s in previous and len(previous[s].get("dates", [])) >= 20 and (s in CNBC_SYM or s in TV_SYM)]
    if not want:
        return []
    q = reserve_quotes(want)
    by_sym = {i["symbol"]: i for i in instruments()}
    filled = []
    for sym in want:
        e, pv = q.get(sym), previous[sym]
        if not e or not e.get("date"):
            continue
        dates, vals = list(pv["dates"]), list(pv["values"])
        x = e["last"]
        if not (vals[-1] / 3 < x < vals[-1] * 3):      # a different basis or contract: keep the previous data
            log(f"  reserve {sym}: {x} rejected against {vals[-1]}")
            continue
        if e["date"] == dates[-1]:
            vals[-1] = x
        elif e["date"] > dates[-1]:
            dates.append(e["date"]); vals.append(x)
        else:
            continue                                       # older than what we have
        s = {"dates": dates, "values": vals, "last": None, "last_time": None}
        ins = by_sym.get(sym) or {k: pv.get(k) for k in ("group", "name_en", "name_no", "kind", "decimals")}
        out[sym] = {**{k: ins[k] for k in ("group", "name_en", "name_no", "kind", "decimals")}, "currency": pv.get("currency"), "last_time": e.get("time"),
                    "dates": dates[-260:], "values": vals[-260:], **stats(s, ins["kind"]), "source": e["source"]}
        filled.append(sym)
    if filled:
        log(f"reserve: {len(filled)} instrument(s) from " + ", ".join(sorted({out[s]['source'] for s in filled})) + " (" + ", ".join(filled) + ")")
    return filled


def fetch_yahoo_spark(previous, rng="5d", heal=False):
    """Every instrument from Yahoo's spark endpoint, twenty symbols a request and the last days (`rng`: five days for the
    intraday runs, a month for the morning run) spliced onto the history kept from the previous run; the full year is only
    fetched for an instrument whose history is missing or short (`heal`, the morning run) or by fetch_yahoo (--full, and
    Sundays). The last close of an open market is its live print, as in the chart endpoint."""
    ins, out, failed, healed = instruments(), {}, [], []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for ci in range(0, len(ins), 20):
        chunk = ins[ci:ci + 20]
        try:
            raw = cached(f"yahoo_spark_ins_{rng}_{ci // 20}.json", 0.2, lambda chunk=chunk: yahoo_get("https://query1.finance.yahoo.com/v8/finance/spark?symbols="
                         + ",".join(urllib.request.quote(i["symbol"]) for i in chunk) + f"&range={rng}&interval=1d"))
            sp = json.loads(raw)
        except Exception as e:
            log(f"  !! yahoo spark chunk {ci // 20}: {repr(e)[:100]}")
            sp = {}
        for i in chunk:
            sym = i["symbol"]; pv = previous.get(sym)
            r = sp.get(sym) or {}
            pts = [(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"), round(float(c), 6)) for t, c in zip(r.get("timestamp") or [], r.get("close") or []) if c is not None]
            if pts and (r.get("close") or [None])[-1] is None:
                # spark leaves the latest bar empty for some markets after their close (the Asian indices): the chart endpoint has it
                try:
                    c = yahoo_chart(sym, "5d")
                    pts = list(zip(c["dates"], c["values"]))
                    if c.get("last") is not None and c.get("last_time") and c["last_time"][:10] > pts[-1][0]:
                        pts.append((c["last_time"][:10], round(float(c["last"]), 6)))
                except Exception as e:
                    log(f"  {sym}: spark bar empty and chart failed ({repr(e)[:60]})")
            if heal and pts and (not pv or len(pv.get("dates", [])) < 200):
                # no usable history from the previous run: this instrument's full year from the chart endpoint
                try:
                    c = yahoo_chart(sym)
                    out[sym] = {**{k: i[k] for k in ("group", "name_en", "name_no", "kind", "decimals")}, "currency": c["currency"], "last_time": c["last_time"],
                                "dates": c["dates"][-260:], "values": c["values"][-260:], **stats(c, i["kind"])}
                    healed.append(sym)
                    continue
                except Exception as e:
                    log(f"  {sym}: full history failed ({repr(e)[:60]})")
            if not pts or not pv or len(pv.get("dates", [])) < 20:
                failed.append(sym)
                if pv:
                    out[sym] = pv
                continue
            keep = [(d, v) for d, v in zip(pv["dates"], pv["values"]) if d < pts[0][0]]
            dates, vals = [d for d, _ in keep] + [d for d, _ in pts], [v for _, v in keep] + [v for _, v in pts]
            while len(vals) > 1 and not (vals[-2] / 3 < vals[-1] < vals[-2] * 3):
                dates.pop(); vals.pop()
            s = {"dates": dates, "values": vals, "last": None, "last_time": None}
            unchanged = pv.get("values") and pv["values"][-1] == vals[-1] and pv.get("dates", [None])[-1] == dates[-1]
            out[sym] = {**{k: i[k] for k in ("group", "name_en", "name_no", "kind", "decimals")}, "currency": pv.get("currency"),
                        "last_time": pv.get("last_time") if unchanged else now, "dates": dates[-260:], "values": vals[-260:], **stats(s, i["kind"])}
    log(f"yahoo spark ({rng}): {len(out) - len(failed)} fetched{', ' + str(len(healed)) + ' with the full year (' + ', '.join(healed) + ')' if healed else ''}, "
        f"{len(failed)} failed{' (' + ', '.join(failed) + ')' if failed else ''}")
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


def fetch_ttf_curve(prev):
    """The TTF gas futures curve (ICE Endex monthly contracts) from TradingView's scanner, which carries no history: each
    run appends today's print to the series kept from the previous run, so the earlier curves build up from 13 Nov 2026.
    (Yahoo lists only two TTF months.) Returns None when the scanner fails, and the previous curve is kept."""
    now = datetime.now(timezone.utc)
    y, m = now.year, now.month
    months = []
    for i in range(1, 25):
        mm = (m - 1 + i) % 12 + 1
        months.append((f"{y + (m - 1 + i) // 12}-{mm:02d}", f"ICEENDEX:TFM{FUT_MONTHS[mm - 1]}{y + (m - 1 + i) // 12}"))
    body = json.dumps({"symbols": {"tickers": [s for _, s in months], "query": {"types": []}}, "columns": ["close", "time"], "range": [0, 100]}).encode()
    req = urllib.request.Request("https://scanner.tradingview.com/global/scan", data=body, headers={"User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        rows = {row["s"]: row["d"] for row in json.loads(r.read()).get("data", [])}
    old = {k["m"]: k for k in (prev or {}).get("contracts", [])}
    contracts = []
    for mth, sym in months:
        d = rows.get(sym)
        if not d or d[0] is None or not d[1]:
            continue
        day = (datetime.fromtimestamp(d[1], tz=timezone.utc) + timedelta(hours=7)).strftime("%Y-%m-%d")
        if (now - datetime.fromtimestamp(d[1], tz=timezone.utc)).days > 5:
            continue                                   # an expired or unquoted contract
        dates, vals = list(old.get(mth, {}).get("dates", [])), list(old.get(mth, {}).get("values", []))
        if dates and dates[-1] == day:
            vals[-1] = round(float(d[0]), 4)
        elif not dates or day > dates[-1]:
            dates.append(day); vals.append(round(float(d[0]), 4))
        contracts.append({"m": mth, "sym": sym, "dates": dates[-130:], "values": vals[-130:]})
    if len(contracts) < 6:
        raise RuntimeError(f"TTF curve: only {len(contracts)} contracts from TradingView")
    return {"name_en": "TTF gas", "name_no": "TTF-gass", "unit": "EUR/MWh", "source": "ICE Endex monthly contracts via TradingView, history kept from 13 Nov 2026", "contracts": contracts}


def fetch_curves(prev=None):
    """Futures curves: Brent from Yahoo's monthly contracts (the next 24 delivery months, each with six months of closes,
    so the page can draw today's curve against earlier ones) and TTF gas from TradingView (fetch_ttf_curve). WTI was
    dropped on 13 Nov 2026 (user: the gas curve's seasonal pattern says more beside a falling Brent curve)."""
    out = {}
    prev = prev or {}
    now = datetime.now(timezone.utc)
    try:
        out["TTF"] = fetch_ttf_curve(prev.get("TTF"))
        log(f"curves TTF: {len(out['TTF']['contracts'])} contracts, {out['TTF']['contracts'][0]['m']} {out['TTF']['contracts'][0]['values'][-1]} … {out['TTF']['contracts'][-1]['m']} {out['TTF']['contracts'][-1]['values'][-1]}, history {len(out['TTF']['contracts'][0]['dates'])} day(s)")
    except Exception as e:
        log(f"  !! curves TTF: {repr(e)[:120]}; keeping the previous curve")
        if prev.get("TTF"):
            out["TTF"] = prev["TTF"]
    for root, name_en, name_no in (("BZ", "Brent", "Brent"),):
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
        if len(contracts) < 6 and prev.get(root):
            log(f"  !! curves {root}: only {len(contracts)} contracts; keeping the previous curve")
            out[root] = prev[root]
            continue
        out[root] = {"name_en": name_en, "name_no": name_no, "unit": "USD/bbl", "contracts": contracts}
        log(f"curves {root}: {len(contracts)} contracts" + (f", {contracts[0]['m']} {contracts[0]['values'][-1]} … {contracts[-1]['m']} {contracts[-1]['values'][-1]}" if contracts else ""))
    return {k: out[k] for k in ("BZ", "TTF") if k in out}


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


def fetch_ppr(mpr):
    """Norges Bank's projections from the Monetary Policy Report's published data set (tallsett-ppr-N-YY.xlsx, linked
    from the report page): the policy rate path (Data A) and I-44 (Data E), quarterly, history and projection. Nothing
    is typed in by hand: the list page gives the latest report, the report page gives the file."""
    raw = cached("nb_mpr_list.html", 12, lambda: get("https://www.norges-bank.no/aktuelt/publikasjoner/Pengepolitisk-rapport/", timeout=60))
    html = raw.decode("utf-8", "ignore")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reports = sorted(set((d, h) for h, d in re.findall(r'href="(/aktuelt/publikasjoner/Pengepolitisk-rapport/20\d\d/[^"]+)"[^<]*>Pengepolitisk rapport \d/20\d\d</a>.*?dateTime="(20\d\d-\d\d-\d\d)', html, flags=re.S)))
    reports = [r for r in reports if r[0] <= today]
    if not reports:
        raise ValueError("no report page found")
    published, href = reports[-1]
    page = cached("nb_ppr_page.html", 12, lambda: get("https://www.norges-bank.no" + href, timeout=60)).decode("utf-8", "ignore")
    m = re.search(r'href="([^"]*tallsett-ppr-(\d)-(\d\d)\.xlsx[^"]*)"', page)
    if not m:
        raise ValueError("no data set link on the report page")
    url = m.group(1); url = url if url.startswith("http") else "https://www.norges-bank.no" + url
    report = f"{m.group(2)}/{m.group(3)}"
    raw = cached(f"nb_ppr_{m.group(2)}_{m.group(3)}.xlsx", 24 * 30, lambda: get(url.split("?")[0], timeout=90))
    def column(sheet, col, prev_col=None):
        pts, prev = [], []
        for r in xlsx_rows(raw, sheet):
            if r and isinstance(r[0], (int, float)) and r[0] > 30000:
                d = excel_date(r[0])
                if len(r) > col and isinstance(r[col], (int, float)):
                    pts.append([d, round(float(r[col]), 2)])
                if prev_col is not None and len(r) > prev_col and isinstance(r[prev_col], (int, float)):
                    prev.append([d, round(float(r[prev_col]), 2)])
        return pts, prev
    policy, policy_prev = column("Data A", 1, 2)
    i44, _ = column("Data E", 1)
    out = {"report": report, "published": published, "url": url.split("?")[0], "policy": policy, "policy_prev": policy_prev, "i44": i44}
    log(f"ppr: report {report} of {published}, policy path {len(policy)} quarters to {policy[-1][0] if policy else '-'} ({policy[-1][1] if policy else '-'}), I-44 {len(i44)} quarters to {i44[-1][0] if i44 else '-'}")
    return out


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
    raw = cached("yahoo_screener_large_caps.json", 2, lambda: yahoo_get(
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
    # a month of daily closes per stock for the hover chart, from Yahoo's spark endpoint (many symbols per call)
    got = 0
    for ci in range(0, len(items), 20):
        chunk = items[ci:ci + 20]
        try:
            raw = cached(f"yahoo_spark_{ci // 20}.json", 2, lambda chunk=chunk: yahoo_get("https://query1.finance.yahoo.com/v8/finance/spark?symbols=" + ",".join(urllib.request.quote(i["s"]) for i in chunk) + "&range=1mo&interval=1d"))
            sp = json.loads(raw)
            for i in chunk:
                r = sp.get(i["s"]) or {}
                closes = [round(float(c), 2) for c in (r.get("close") or []) if c is not None]
                if len(closes) >= 5:
                    i["m1"] = closes[-23:]
                    got += 1
        except Exception as e:
            log(f"  heatmap spark chunk {ci // 20}: {repr(e)[:100]}")
    log(f"heatmap: {len(items)} stocks, latest print {max(i['t'] or '' for i in items)}, month series for {got}")
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
        x, y = yahoo.get(a), yahoo.get(b)
        fresher = x and y and (cur or {}).get("last_date") and min(x.get("last_date") or "", y.get("last_date") or "") > cur["last_date"]
        if cur and len(cur.get("dates", [])) >= 10 and not fresher:
            continue
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


US_SESSION = [("SPY", "S&P 500 (SPY ETF)", "S&P 500 (SPY-ETF)"), ("QQQ", "Nasdaq 100 (QQQ ETF)", "Nasdaq 100 (QQQ-ETF)"),
              ("DIA", "Dow Jones (DIA ETF)", "Dow Jones (DIA-ETF)"), ("IWM", "Russell 2000 (IWM ETF)", "Russell 2000 (IWM-ETF)")]
US_FUTURES = [("ES=F", "S&P 500 futures", "S&P 500-futures"), ("NQ=F", "Nasdaq 100 futures", "Nasdaq 100-futures"),
              ("YM=F", "Dow futures", "Dow-futures"), ("RTY=F", "Russell 2000 futures", "Russell 2000-futures")]


def fetch_us_session():
    """The US session outside regular hours. The index ETFs (SPY, QQQ, DIA, IWM) trade before the open (09:00-14:30 UTC) and
    after the close (21:00-01:00 UTC), and Yahoo's chart endpoint returns those prints with includePrePost; the indices
    themselves have none. The index futures trade almost round the clock and carry the overnight move. Read by the
    US-open and US-close intraday runs (INTRADAY.md) and shown on the Intraday report page."""
    stamp = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    etf, need = {}, []
    for sym, en, no in US_SESSION:
        try:
            raw = cached(f"yahoo_pp_{sym}.json", 0.2, lambda sym=sym: yahoo_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=5m&includePrePost=true"))
        except Exception as e:
            log(f"  {sym}: {repr(e)[:80]}")
            need.append((sym, en, no))
            continue
        res = json.loads(raw)["chart"]["result"][0]
        meta, ts = res["meta"], res.get("timestamp") or []
        closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        per = meta.get("currentTradingPeriod") or {}
        r0, r1 = (per.get("regular") or {}).get("start"), (per.get("regular") or {}).get("end")
        pts = [(t, c) for t, c in zip(ts, closes) if c is not None]
        pre = [p for p in pts if r0 and p[0] < r0]
        post = [p for p in pts if r1 and p[0] >= r1]
        close, close_t = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        e = {"name_en": en, "name_no": no, "close": close, "close_time": stamp(close_t) if close_t else None, "prev_close": prev, "d1": pct(close, prev),
             "session_date": datetime.fromtimestamp(r0, tz=timezone.utc).strftime("%Y-%m-%d") if r0 else None}
        if post:
            e.update(post=round(float(post[-1][1]), 4), post_time=stamp(post[-1][0]), post_chg=pct(post[-1][1], close))
        if pre:
            # before the open, Yahoo's regularMarketPrice is still the previous close, so the pre-market change is measured from it
            ref = close if (close_t and r0 and close_t < r0) else prev
            e.update(pre=round(float(pre[-1][1]), 4), pre_time=stamp(pre[-1][0]), pre_chg=pct(pre[-1][1], ref))
        etf[sym] = e
    sources = {"Yahoo Finance"} if etf else set()
    if need:
        # Yahoo refused: the regular close and the extended-hours print from CNBC or TradingView
        q = reserve_quotes([s for s, _, _ in need])
        h = datetime.now(timezone.utc).hour
        for sym, en, no in need:
            r = q.get(sym)
            if not r:
                continue
            e = {"name_en": en, "name_no": no, "close": r["last"], "close_time": r.get("time"), "prev_close": r.get("prev"),
                 "d1": pct(r["last"], r.get("prev")) if r.get("prev") != r["last"] else None,     # before the open CNBC's last is the previous close
                 "session_date": r.get("date"), "source": r["source"]}
            if r.get("ext_type") == "POST_MKT":
                e.update(post=r["ext"], post_time=r.get("ext_time"), post_chg=pct(r["ext"], r["last"]))
            elif r.get("ext_type") == "PRE_MKT":
                e.update(pre=r["ext"], pre_time=r.get("ext_time"), pre_chg=pct(r["ext"], r["last"]))
            elif r["source"] == "TradingView":
                # the scanner carries both prints without a time: after the close (from 20 UTC) the post-market one, before the open the pre-market one
                if (h >= 20 or h < 2) and r.get("post") is not None:
                    e.update(post=r["post"], post_time=r.get("time"), post_chg=round(r["post_chg"], 3) if r.get("post_chg") is not None else pct(r["post"], r["last"]))
                elif 8 <= h < 15 and r.get("pre") is not None:
                    e.update(pre=r["pre"], pre_time=r.get("time"), pre_chg=round(r["pre_chg"], 3) if r.get("pre_chg") is not None else pct(r["pre"], r["last"]))
            etf[sym] = e; sources.add(r["source"])
    fut, need = {}, []
    for sym, en, no in US_FUTURES:
        try:
            s = yahoo_chart(sym, "5d")
            fut[sym] = {"name_en": en, "name_no": no, "last": s["last"], "last_time": s["last_time"], "prev_close": s["prev_close"], "d1": pct(s["last"], s["prev_close"])}
        except Exception as e:
            log(f"  {sym}: {repr(e)[:80]}")
            need.append((sym, en, no))
    if need:
        q = reserve_quotes([s for s, _, _ in need])
        for sym, en, no in need:
            r = q.get(sym)
            if r:
                fut[sym] = {"name_en": en, "name_no": no, "last": r["last"], "last_time": r.get("time"), "prev_close": r.get("prev"), "d1": pct(r["last"], r.get("prev")), "source": r["source"]}
                sources.add(r["source"])
    return {"etf": etf, "futures": fut, "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "source": ", ".join(sorted(sources)) + "; the ETFs stand in for the indices, which have no pre- or post-market prints"
                      + ("; reserve prints are delayed up to 15 minutes" if sources - {"Yahoo Finance"} else "")}


def main():
    prev = json.loads((DATA / "data.json").read_text(encoding="utf-8")) if (DATA / "data.json").exists() else {}
    if not prev:
        # data.json is not versioned: a fresh clone takes the previous run's data from the live site, so the fallbacks work
        try:
            raw = get("https://market-summary.jskjeltorp.workers.dev/data.js", timeout=60).decode("utf-8")
            prev = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
            log(f"previous data taken from the live site ({prev.get('asof')})")
        except Exception as e:
            log(f"no previous data ({repr(e)[:80]})")
    if "--offline" in sys.argv:
        # the sources are unreachable (or the time budget is spent): write the previous run's data as it is, dated as it was
        if not prev:
            log("!! --offline: no previous data to write"); return 1
        prev["kept_from_previous"] = ["all blocks (--offline: the previous run's data, as of " + str(prev.get("asof")) + ")"]
        (DATA / "data.json").write_text(json.dumps(prev, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        log(f"wrote data/data.json from the previous run ({prev.get('asof')}), nothing refreshed")
        return 0
    kept = []
    # Yahoo: the full year of every instrument only with --full, on Sundays (a weekly heal) or without previous data; otherwise
    # the last month (the morning run) or five days (--intraday) in four requests, spliced onto the previous run's history
    full = "--full" in sys.argv or len(prev.get("yahoo", {})) < 20 or (datetime.now(timezone.utc).weekday() == 6 and not INTRADAY)
    intraday = INTRADAY and not full
    mode = "intraday" if intraday else "full" if full else "incremental"
    if INTRADAY and full:
        log("!! --intraday needs the previous run's data; running the full fetch")
    log("1. Yahoo (" + {"intraday": "spark, five days per instrument", "incremental": "spark, a month per instrument, spliced onto the previous history", "full": "chart, the full year per instrument"}[mode] + ")")
    yahoo, failed = fetch_yahoo(prev.get("yahoo", {})) if full else fetch_yahoo_spark(prev["yahoo"], "5d" if intraday else "1mo", heal=not intraday)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    reserve = apply_reserve(yahoo, failed, prev.get("yahoo", {})) if failed else []
    failed = [s for s in failed if s not in reserve]
    prev_asof = prev.get("yahoo_asof") or prev.get("asof")
    yahoo_asof = now
    if len(failed) > len(yahoo) / 2 and prev_asof:
        yahoo_asof = prev_asof     # most prices are the previous run's: dated as such for the brief
    if reserve:
        kept.append(f"yahoo: {len(reserve)} instrument(s) from the reserve source(s) {', '.join(sorted({yahoo[s]['source'] for s in reserve}))} (Yahoo refused; delayed up to 15 minutes, as of {now})")
    if failed:
        kept.append(f"yahoo: {len(failed)} instrument(s) kept from the previous run ({prev_asof}): {', '.join(failed[:15])}{', …' if len(failed) > 15 else ''}")
    status = {"fetched": len(yahoo) - len(failed) - len(reserve), "reserve": len(reserve), "reserve_sources": sorted({yahoo[s]["source"] for s in reserve}),
              "reserve_symbols": reserve, "kept": len(failed), "kept_symbols": failed, "prev_asof": prev_asof, "mode": mode}
    if intraday:
        log("1b. Dated Brent and UK gas: as the morning run left them")
        for k, v in prev["yahoo"].items():
            if k not in yahoo and k not in CROSSES:
                yahoo[k] = v
    else:
        log("1b. Dated Brent and UK gas")
        fetch_extra_commodities(yahoo, prev.get("yahoo", {}))
    derive_crosses(yahoo)
    for sym, offset, anchor in (("BRENT_DATED", 0, "BZ=F"), ("UK_SAP", 1, "TTF=F")):
        # the weekly series next to the futures they belong with: Dated Brent before the first Brent contract, NBP after TTF
        if sym in yahoo and anchor in yahoo:
            keys = [k for k in yahoo if k != sym]
            keys.insert(keys.index(anchor) + offset, sym)
            yahoo = {k: yahoo[k] for k in keys}
    blocks = {}
    daily = ("fred", "ecb", "nb", "ssb", "bis", "macro", "fedfunds", "gov", "curves", "eer", "mpr")
    for name, fn in (("fred", fetch_fred), ("ecb", fetch_ecb), ("nb", fetch_nb), ("ssb", fetch_ssb), ("bis", fetch_bis), ("macro", fetch_macro), ("fedfunds", fetch_fedfunds), ("gov", fetch_gov), ("curves", lambda: fetch_curves(prev.get("curves", {}))), ("eer", fetch_eer), ("mpr", fetch_mpr), ("us_session", fetch_us_session), ("news", fetch_news), ("heatmap", fetch_heatmap)):
        if intraday and name in daily:
            blocks[name] = prev.get(name, {})
            continue
        if name == "us_session" and not intraday:
            blocks[name] = {}        # the US-session card belongs to the (paused) intraday view; twelve Yahoo requests saved
            continue
        log(f"{name}")
        try:
            blocks[name] = fn()
        except Exception as e:
            log(f"  !! {name} failed: {repr(e)[:200]}; keeping the previous data")
            blocks[name] = prev.get(name, {})
            kept.append(name)
    if intraday:
        blocks["ppr"] = prev.get("ppr", {})
        kept.append(f"daily blocks (FRED, ECB, Norges Bank, SSB, BIS, macro, fed funds, government curves, MPR) as the morning run left them ({prev.get('asof')}); intraday run")
        log("daily blocks: as the morning run left them")
    else:
        try:
            fetch_r12m(blocks)
        except Exception as e:
            log(f"  !! r12m failed: {repr(e)[:200]}")
        try:
            fetch_pce(blocks)
        except Exception as e:
            log(f"  !! pce failed: {repr(e)[:200]}")
        try:
            blocks["ppr"] = fetch_ppr(blocks.get("mpr"))
        except Exception as e:
            log(f"  !! ppr failed: {repr(e)[:200]}; keeping the previous data")
            blocks["ppr"] = prev.get("ppr", {})
            kept.append("ppr")
    try:
        blocks["derived"] = derive(blocks)
    except Exception as e:
        log(f"  !! derived failed: {repr(e)[:200]}")
        blocks["derived"] = prev.get("derived", {})
    data = {"asof": now, "yahoo_asof": yahoo_asof, "yahoo_status": status, "yahoo": yahoo, **blocks,
            "policy_rates": read_csv("policy_rates.csv"), "events": read_csv("events.csv"), "cpi_consensus": read_csv("cpi_consensus.csv"), "cb_minutes": read_json("cb_minutes.json", {}), "kept_from_previous": kept}
    (DATA / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"wrote data/data.json ({(DATA / 'data.json').stat().st_size // 1024} kB); kept from previous: {kept or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
