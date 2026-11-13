"""Build the site from template.html: site/index.html (markup and styles), site/data.js (the data, the reports and the
alerts as window.__D) and site/app.js (the script). The three are cache-busted by a build stamp, so the browser keeps the
script and Plotly between visits and refetches only what changed.

    python -X utf8 pipeline/fetch.py     # the data
    python -X utf8 build.py              # this
    npx wrangler deploy                  # Cloudflare Workers static assets, worker "market-summary"

Letters live in data/letters/YYYY-MM-DD/<kind>.<lang>.md with kind in highlights, markets, macro, politics, norgesbank and lang in en, no.
Intraday snapshots are data/letters/YYYY-MM-DD/intraday-HHMM.<lang>.md (Oslo time); every snapshot of the day is kept."""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KINDS = ("highlights", "markets", "intraday", "macro", "politics", "norgesbank", "week")


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
            m = re.fullmatch(r"(highlights|markets|intraday|macro|politics|norgesbank|week)\.(en|no)\.md", p.name)
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


LIVE = "https://market-summary.jskjeltorp.workers.dev"


def previous_data():
    """The newer of data/data.json and the live site's copy (the file is not versioned). A build on a machine whose local
    copy is older than what the site serves would otherwise overwrite a fresh run's data with stale prices on deploy
    (13 Nov 2026: a local rebuild of the reports put the previous evening's data back on the site, and the page warned)."""
    p = ROOT / "data" / "data.json"
    local = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    live = None
    try:
        import urllib.request
        raw = urllib.request.urlopen(urllib.request.Request(LIVE + "/data.js", headers={"User-Agent": "market-summary/0.1"}), timeout=60).read().decode("utf-8")
        live = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        for k in ("letters", "alerts", "generated"):
            live.pop(k, None)
    except Exception as e:
        print(f"live data not reachable ({repr(e)[:80]})")
    if live and (not local or (live.get("asof") or "") > (local.get("asof") or "")):
        p.write_text(json.dumps(live, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"data/data.json taken from the live site ({live.get('asof')}), newer than the local copy ({(local or {}).get('asof')})")
        return live
    if not local:
        raise SystemExit("no data: neither data/data.json nor the live site")
    return local


def lint_latest():
    """Run the report linter on the newest day on file and print its findings (the routines fix them before building)."""
    import subprocess
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "pipeline" / "lint.py")], capture_output=True, text=True, encoding="utf-8", timeout=60)
        out = (r.stdout or "").strip()
        if out:
            print(out)
    except Exception as e:
        print(f"lint skipped: {e!r}")


def main():
    data = previous_data()
    data["letters"] = letters()
    ap = ROOT / "data" / "alerts.json"
    data["alerts"] = json.loads(ap.read_text(encoding="utf-8")) if ap.exists() else {"updated": None, "events": []}
    data["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    template = (ROOT / "template.html").read_text(encoding="utf-8")
    if "/*__DATA__*/null" not in template:
        sys.exit("template.html lacks the /*__DATA__*/null marker")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    # the script between the main <script> and </script> becomes app.js; the data becomes data.js
    i = template.index("<script>\nconst D = /*__DATA__*/null;")
    j = template.index("</script>", i)
    script = template[i + len("<script>"):j].replace("const D = /*__DATA__*/null;", "const D = window.__D;", 1)
    page = template[:i] + f'<script src="data.js?v={stamp}" defer></script>\n<script src="app.js?v={stamp}" defer></script>' + template[j + len("</script>"):]
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    site = ROOT / "site"; site.mkdir(exist_ok=True)
    (site / "index.html").write_text(page, encoding="utf-8")
    (site / "data.js").write_text("window.__D = " + payload + ";\n", encoding="utf-8")
    (site / "app.js").write_text(script, encoding="utf-8")
    lint_latest()
    print(f"wrote site/index.html ({len(page) // 1024} kB), data.js ({len(payload) // 1024} kB), app.js ({len(script) // 1024} kB); letters for {', '.join(data['letters']) or 'no days yet'}")


if __name__ == "__main__":
    main()
