"""Build site/index.html: inline data/data.json and the letters into template.html.

    python -X utf8 pipeline/fetch.py     # the data
    python -X utf8 build.py              # this
    npx wrangler deploy                  # Cloudflare Workers static assets, worker "market-summary"

Letters live in data/letters/YYYY-MM-DD/<kind>.<lang>.md with kind in markets, macro, politics and lang in en, no.
Intraday snapshots are data/letters/YYYY-MM-DD/intraday-HHMM.<lang>.md (Oslo time); every snapshot of the day is kept."""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KINDS = ("markets", "intraday", "macro", "politics", "norgesbank")


def letters():
    out = {}
    base = ROOT / "data" / "letters"
    for day in sorted(p for p in base.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)):
        entry, snaps = {}, {}
        # a plain intraday.<lang>.md (older playbook wording) is renamed to a timed snapshot, so the next run cannot
        # overwrite it; the time comes from its heading ("as of 12:05" / "per kl. 12.05")
        for lang in ("en", "no"):
            p = day / f"intraday.{lang}.md"
            if p.exists():
                first = (p.read_text(encoding="utf-8").splitlines() or [""])[0]
                hm = re.search(r"(\d{1,2})[:.](\d{2})\s*(?:Oslo|$)", first) or re.search(r"(\d{1,2})[:.](\d{2})", first)
                if hm:
                    p.rename(day / f"intraday-{int(hm.group(1)):02d}{hm.group(2)}.{lang}.md")
        for p in day.glob("*.md"):
            m = re.fullmatch(r"(markets|intraday|macro|politics|norgesbank)\.(en|no)\.md", p.name)
            if m:
                entry.setdefault(m.group(1), {})[m.group(2)] = p.read_text(encoding="utf-8")
            # intraday snapshots: intraday-HHMM.<lang>.md (Oslo time), one pair per run, all kept
            m = re.fullmatch(r"intraday-(\d{4})\.(en|no)\.md", p.name)
            if m:
                snaps.setdefault(m.group(1), {"time": m.group(1)})[m.group(2)] = p.read_text(encoding="utf-8")
        if "intraday" in entry:   # a legacy single intraday file counts as one snapshot, timed from its heading
            txt = entry["intraday"].get("en") or entry["intraday"].get("no") or ""
            hm = re.search(r"(\d{1,2})[:.](\d{2})", txt.splitlines()[0] if txt else "")
            key = f"{int(hm.group(1)):02d}{hm.group(2)}" if hm else "0000"
            snaps.setdefault(key, {"time": key, **entry["intraday"]})
        if snaps:
            entry["intraday_snaps"] = [snaps[k] for k in sorted(snaps, reverse=True)]   # newest first
            entry["intraday"] = {k: v for k, v in entry["intraday_snaps"][0].items() if k != "time"}
        if entry:
            out[day.name] = entry
    return out


def main():
    data = json.loads((ROOT / "data" / "data.json").read_text(encoding="utf-8"))
    data["letters"] = letters()
    ap = ROOT / "data" / "alerts.json"
    data["alerts"] = json.loads(ap.read_text(encoding="utf-8")) if ap.exists() else {"updated": None, "events": []}
    data["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    template = (ROOT / "template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    if "/*__DATA__*/null" not in template:
        sys.exit("template.html lacks the /*__DATA__*/null marker")
    page = template.replace("/*__DATA__*/null", payload)
    (ROOT / "site").mkdir(exist_ok=True)
    (ROOT / "site" / "index.html").write_text(page, encoding="utf-8")
    print(f"wrote site/index.html ({len(page) // 1024} kB), letters for {', '.join(data['letters']) or 'no days yet'}")


if __name__ == "__main__":
    main()
