"""Norges Bank refresh: fetch Norges Bank's daily series (government yields, bills, the policy rate, the I-44) and, when any of
them has a newer observation than the live site, rebuild and deploy the site. Nothing else changes and nothing is committed
(data/data.json is not versioned). Norges Bank publishes the daily yields the next business day around noon and the I-44
around 16:00, so the 06:05 morning run has the yields two business days old; this runs on weekday afternoons.

    python -X utf8 pipeline/nb_refresh.py            # fetch, compare, deploy if newer
    python -X utf8 pipeline/nb_refresh.py --force    # deploy even without new figures (a test)

The deploy command is `npx wrangler deploy` (the cloud routine) or the command in the WRANGLER environment variable.
Exit code 0 when nothing was needed or the deploy succeeded, 1 on a failure."""
import json, os, subprocess, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = "https://market-summary.jskjeltorp.workers.dev"
KEYS = ("nok_10y", "nok_3y", "nok_3m", "nok_12m", "nok_policy", "nok_i44")


def run(cmd, **kw):
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, text=True, encoding="utf-8", errors="replace", **kw)


def live_dates():
    raw = urllib.request.urlopen(urllib.request.Request(LIVE + "/data.js", headers={"User-Agent": "market-summary/0.1"}), timeout=60).read().decode("utf-8")
    d = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    return {k: (d.get("nb", {}).get(k) or {}).get("last_date") for k in KEYS}, d.get("asof")


def main():
    force = "--force" in sys.argv
    before, asof = live_dates()
    print(f"live site: data as of {asof}; Norges Bank dates: " + ", ".join(f"{k} {v}" for k, v in before.items()))
    r = run([sys.executable, "-X", "utf8", "pipeline/fetch.py", "--nb-only"], capture_output=True)
    print(r.stdout[-1500:])
    if r.returncode != 0:
        print(r.stderr[-1500:]); print("fetch failed; nothing deployed"); return 1
    d = json.loads((ROOT / "data" / "data.json").read_text(encoding="utf-8"))
    after = {k: (d.get("nb", {}).get(k) or {}).get("last_date") for k in KEYS}
    newer = [k for k in KEYS if after.get(k) and (not before.get(k) or after[k] > before[k])]
    print("new observations: " + (", ".join(f"{k} {before.get(k)} -> {after[k]}" for k in newer) if newer else "none"))
    if not newer and not force:
        print("nothing to deploy"); return 0
    r = run([sys.executable, "-X", "utf8", "build.py"], capture_output=True)
    print(r.stdout[-800:])
    if r.returncode != 0:
        print(r.stderr[-1500:]); print("build failed; nothing deployed"); return 1
    cmd = os.environ.get("WRANGLER", "npx wrangler deploy").split()
    r = run(cmd, capture_output=True)
    out = (r.stdout or "") + (r.stderr or "")
    print(out[-1200:])
    if r.returncode != 0 or LIVE not in out:
        print("deploy failed"); return 1
    print("deployed " + LIVE + " with Norges Bank figures: " + ", ".join(f"{k} {after[k]}" for k in KEYS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
