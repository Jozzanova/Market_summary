"""Build site/index.html: inline data/data.json and the letters into template.html.

    python -X utf8 pipeline/fetch.py     # the data
    python -X utf8 build.py              # this
    npx wrangler deploy                  # Cloudflare Workers static assets, worker "market-summary"

Letters live in data/letters/YYYY-MM-DD/<kind>.<lang>.md with kind in markets, macro, politics and lang in en, no."""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KINDS = ("markets", "macro", "politics")


def letters():
    out = {}
    base = ROOT / "data" / "letters"
    for day in sorted(p for p in base.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)):
        entry = {}
        for p in day.glob("*.md"):
            m = re.fullmatch(r"(markets|macro|politics)\.(en|no)\.md", p.name)
            if m:
                entry.setdefault(m.group(1), {})[m.group(2)] = p.read_text(encoding="utf-8")
        if entry:
            out[day.name] = entry
    return out


def main():
    data = json.loads((ROOT / "data" / "data.json").read_text(encoding="utf-8"))
    data["letters"] = letters()
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
