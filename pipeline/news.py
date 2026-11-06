#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""news.py - recent headlines with snippets from the public RSS feeds of the main financial outlets and central banks.

    python -X utf8 pipeline/news.py            # fetch, print a summary, write rawData/news.json
    (fetch.py calls fetch_news() and puts the result in data/data.json under "news")

Standard library only. Each feed is fetched with a short timeout; a feed that fails is skipped and listed. Items older
than MAX_AGE_H are dropped, duplicates (same link or same title) removed, HTML stripped from snippets, and the list is
sorted newest first. Sources carry a category so the page can filter: markets, economy, central bank, norway, politics.
"""
import html
import json
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "market-summary/0.1 admin@jskjeltorp.workers.dev"
MAX_AGE_H = 36
MAX_PER_FEED = 25
ATOM = "{http://www.w3.org/2005/Atom}"

# (source, url, category, language)
FEEDS = [
    ("FT", "https://www.ft.com/rss/home", "markets", "en"),
    ("FT", "https://www.ft.com/markets?format=rss", "markets", "en"),
    ("FT", "https://www.ft.com/world?format=rss", "politics", "en"),
    ("WSJ", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", "markets", "en"),
    ("WSJ", "https://feeds.content.dowjones.io/public/rss/socialeconomyfeed", "economy", "en"),
    ("WSJ", "https://feeds.content.dowjones.io/public/rss/RSSWorldNews", "politics", "en"),
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss", "markets", "en"),
    ("Bloomberg", "https://feeds.bloomberg.com/economics/news.rss", "economy", "en"),
    ("Bloomberg", "https://feeds.bloomberg.com/politics/news.rss", "politics", "en"),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html", "markets", "en"),
    ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html", "markets", "en"),
    ("The Economist", "https://www.economist.com/finance-and-economics/rss.xml", "economy", "en"),
    ("The Guardian", "https://www.theguardian.com/uk/business/rss", "economy", "en"),
    ("E24", "https://e24.no/rss", "norway", "no"),
    ("ECB", "https://www.ecb.europa.eu/rss/press.html", "central bank", "en"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "central bank", "en"),
    ("Bank of England", "https://www.bankofengland.co.uk/rss/news", "central bank", "en"),
    # outlets without a public feed, through Google News search feeds (headline and link only, no snippet)
    ("Reuters", "https://news.google.com/rss/search?q=site:reuters.com+(markets+OR+economy+OR+%22central+bank%22)&hl=en-US&gl=US&ceid=US:en", "markets", "en"),
    ("DN", "https://news.google.com/rss/search?q=site:dn.no&hl=no&gl=NO&ceid=NO:no", "norway", "no"),
    ("Finansavisen", "https://news.google.com/rss/search?q=site:finansavisen.no&hl=no&gl=NO&ceid=NO:no", "norway", "no"),
    ("Norges Bank", "https://news.google.com/rss/search?q=site:norges-bank.no&hl=no&gl=NO&ceid=NO:no", "central bank", "no"),
]
GOOGLE = "news.google.com"


def log(msg):
    print(msg, flush=True)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def clean(text, limit=320):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def when(item):
    for tag in ("pubDate", "{http://purl.org/dc/elements/1.1/}date", ATOM + "published", ATOM + "updated", "published", "updated"):
        v = item.findtext(tag)
        if v:
            v = v.strip()
            try:
                dt = parsedate_to_datetime(v)
            except Exception:
                try:
                    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                except Exception:
                    continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    return None


def parse(raw, source, category, lang):
    root = ET.fromstring(raw)
    items = root.findall(".//item") or root.findall(".//" + ATOM + "entry")
    out = []
    for it in items[:MAX_PER_FEED]:
        title = clean(it.findtext("title") or it.findtext(ATOM + "title") or "", 200)
        link = (it.findtext("link") or "").strip()
        if not link:
            a = it.find(ATOM + "link")
            link = (a.get("href") if a is not None else "") or ""
        desc = clean(it.findtext("description") or it.findtext(ATOM + "summary") or it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or "")
        if GOOGLE in link:   # Google News items: the description is a list of related headlines, and the title ends with " - Source"
            desc = ""
            title = re.sub(r"\s+-\s+[^-]{2,40}$", "", title)
        if desc.lower().startswith(title.lower()[:40]) and len(desc) <= len(title) + 5:
            desc = ""
        dt = when(it)
        if not title or not link:
            continue
        out.append({"source": source, "category": category, "lang": lang, "title": title, "link": link, "snippet": desc,
                    "time": dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None})
    return out


def fetch_news():
    now = datetime.now(timezone.utc)
    items, failed, seen_links, seen_titles = [], [], set(), set()
    for source, url, category, lang in FEEDS:
        try:
            got = parse(get(url), source, category, lang)
        except Exception as e:
            failed.append(f"{source} ({url.split('/')[2]}): {repr(e)[:80]}")
            continue
        n = 0
        for it in got:
            if it["time"]:
                age_h = (now - datetime.strptime(it["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() / 3600
                if age_h > MAX_AGE_H or age_h < -6:
                    continue
            key_t = re.sub(r"\W+", "", it["title"].lower())[:60]
            if it["link"] in seen_links or key_t in seen_titles:
                continue
            seen_links.add(it["link"]); seen_titles.add(key_t)
            items.append(it); n += 1
        log(f"  {source:<16} {n:>3} items from {url.split('/')[2]}")
    items.sort(key=lambda x: x["time"] or "", reverse=True)
    result = {"fetched": now.strftime("%Y-%m-%d %H:%M UTC"), "items": items, "failed": failed,
              "sources": sorted({f[0] for f in FEEDS})}
    log(f"news: {len(items)} items from {len(result['sources'])} sources; failed: {failed or 'none'}")
    return result


if __name__ == "__main__":
    res = fetch_news()
    (ROOT / "rawData").mkdir(exist_ok=True)
    (ROOT / "rawData" / "news.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for it in res["items"][:15]:
        print(f"{it['time'] or '?':<21} {it['source']:<14} {it['title'][:90]}")
    sys.exit(0)
