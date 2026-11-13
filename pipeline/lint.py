#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""lint.py - checks the day's reports against the house rules in DAILY.md and INTRADAY.md before they are published.

    python -X utf8 pipeline/lint.py [YYYY-MM-DD]      # default: the newest day on file

Errors (exit code 1) are things the reports must never contain: investingLive citations, equity index levels, prices
from lagged series. Warnings are wording the Norwegian register avoids. The routines fix every finding and rerun until
the output is clean; build.py prints the findings for the newest day."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LETTERS = ROOT / "data" / "letters"

INDEX = r"(S&P ?500|Nasdaq(?: Composite| 100)?|Dow(?: Jones)?|OSEBX|Oslo Børs|Hovedindeksen|STOXX(?: Europe)? ?600|Euro Stoxx ?50|DAX|FTSE ?100|CAC ?40|Nikkei(?: 225)?|Hang Seng|KOSPI|Shanghai|SOX|Russell ?2000|MSCI \w+)"
LEVEL = r"\d{1,3}(?:[  ,.]\d{3})+(?:[.,]\d+)?"
RULES = [
    # (severity, languages, pattern, message)
    ("error", "en no", r"investinglive", "investingLive is banned as a source"),
    ("error", "en no", INDEX + r"[^.\n]{0,70}?\b(?:to|at|til|på|ved|i)\s+" + LEVEL, "equity index level; give the percentage change only"),
    ("error", "en no", r"(?i)\b(dated brent|NBP|system average price|systemgjennomsnittspris)\b", "lagged energy series (Dated Brent, NBP) must not be quoted"),
    ("error", "en no", r"(?i)\b(effective exchange rates?|effektive? valutakurs\w*|NFCI)\b", "lagged series (BIS effective rates, NFCI) must not be quoted"),
    ("warn", "no", r"\bpå forventninger om\b", "write 'som følge av forventninger om'"),
    ("warn", "no", r"\d,\d+ prosentpoeng\b", "Norges Bank writes 'prosentenheter' for rate differences"),
    ("warn", "no", r"\b(steg|falt|stiger|faller)\s+\w*\s*i kurs\b", "bond moves are written as rate moves ('rentene falt')"),
    ("warn", "no", r"\bfast (kjerne)?inflasjon\b", "'firm inflation' is 'vedvarende høy inflasjon'"),
    ("warn", "no", r"\b(fram|framover|framfor|framtid\w*|framleis)\b", "use the 'frem' forms (frem, fremover, fremfor)"),
    ("warn", "no", r"(?i)\bhigh[ -]yield\b", "write 'høyrenteobligasjoner'"),
    ("warn", "no", r"(?i)\bhardliner\b", "anglicism"),
    ("warn", "no", r"(?i)\bfutures priser\b", "write 'markedsprisingen tilsier'"),
    ("warn", "no", r"(?i)\binnstramming på \d+ basispunkter\b", "a rate rise is 'en heving på … basispunkter'"),
    ("warn", "no", r"\bkontantmarkedet\b", "write 'statsobligasjonsmarkedet'"),
    ("warn", "no", r"\brullert\w*\b", "'rotate' is 'rotere' (roterte ut av), not 'rullere'"),
    ("warn", "no", r"\b(et|en|ett) (duete|haukete)\b", "name the person and describe the position instead of 'et duete/haukete medlem'"),
    ("warn", "en", r"(?i)\ba (dovish|hawkish) (board |committee |fed |fomc )?(member|official|governor|policymaker)\b", "name the official and say what they argued"),
    ("warn", "en no", r"\d\s?%(?!\s*\))", "write 'per cent' / 'prosent' in running text"),
    ("warn", "no", r"\bbasispoeng\b", "Norges Bank writes 'basispunkter'"),
    ("warn", "no", r"\b(ti|fem|to|tretti)-års(rente|renten)\b", "write it as one word: tiårsrenten, femårsrenten"),
    ("warn", "no", r"\b(henger etter|ligger etter|er forsinket)\b", "a lagged series is left out, not described as lagging"),
    ("warn", "en", r"\b(lags (the move|behind)|is lagged|is behind the move)\b", "a lagged series is left out, not described as lagging"),
    ("warn", "no", r"(?i)\b(markedsprisingen|markedet) (tilsier|priser( inn)?) en (heving|senkning|renteheving|rentesenkning|kutt)\b", "name the central bank: 'Markedsprisingen tilsier at Fed hever med …'"),
    ("warn", "no", r"\bVIX\b[^.\n]{0,50}\bprosent\b", "VIX moves are given in points, not per cent"),
    ("warn", "en", r"\bVIX\b[^.\n]{0,50}\bper cent\b", "VIX moves are given in points, not per cent"),
    ("warn", "no", r"\b(jeg|meg|mitt|mine)\b", "no first person: the report speaks for the service ('som lot seg verifisere', not 'som jeg kunne verifisere')"),
    ("warn", "en", r"\b(I|I'm|I've|I'd|me|my)\b(?!-44)", "no first person: the report speaks for the service ('that could be verified', not 'that I could verify')"),
]


def check(path, lang):
    text = path.read_text(encoding="utf-8")
    # tables and chart/bars directives are exempt from the running-text rules
    body = "\n".join(l for l in text.splitlines() if not l.startswith("|") and not l.startswith("{"))
    body = re.sub(r"\((https?://[^)\s]+)\)", "()", body)      # link targets are not prose
    out = []
    for sev, langs, pat, msg in RULES:
        if lang not in langs.split():
            continue
        for m in re.finditer(pat, body):
            line = body.count("\n", 0, m.start()) + 1
            out.append((sev, f"{path.name}:{line}", msg, body[max(0, m.start() - 40):m.end() + 20].replace("\n", " ")))
    return out


def main():
    days = sorted(p.name for p in LETTERS.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name))
    day = sys.argv[1] if len(sys.argv) > 1 else (days[-1] if days else None)
    if not day or not (LETTERS / day).exists():
        print("lint: no reports to check"); return 0
    findings = []
    for p in sorted((LETTERS / day).glob("*.md")):
        m = re.search(r"\.(en|no)\.md$", p.name)
        if m:
            findings += check(p, m.group(1))
    errors = [f for f in findings if f[0] == "error"]
    if not findings:
        print(f"lint {day}: clean"); return 0
    print(f"lint {day}: {len(errors)} error(s), {len(findings) - len(errors)} warning(s)")
    for sev, where, msg, ctx in findings:
        print(f"  {sev:<5} {where:<28} {msg}\n        …{ctx}…")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
