// The market summary worker: serves the static page and runs the five-minute alert sniffer.
//
//   scheduled()  every 5 minutes (wrangler.jsonc "triggers"): fetches the latest prints for the instruments in RULES
//                from Yahoo Finance and the I-44 fix from Norges Bank, compares with the previous close, scans a few
//                headline feeds for shock keywords, and keeps the last 20 events.
//   fetch()      GET /api/alerts returns that list as JSON; everything else is the static site (binding ASSETS).
//
// State lives in a Durable Object (binding ALERTS_DO, class AlertsStore, one instance named "main").
// Event ids follow pipeline/alerts.py (symbol:YYYY-MM-DD for a data alert, feed:<link hash> for a headline), so the
// page can merge the live list with the repository's data/alerts.json without duplicates.

const KEEP = 20;
const UA = "market-summary/0.1 admin@jskjeltorp.workers.dev";
const YQ = s => `https://finance.yahoo.com/quote/${encodeURIComponent(s)}`;
// symbol: [label_en, label_no, kind, medium, high, unit, source, url]
const RULES = {
  "EURNOK=X": ["EUR/NOK", "EUR/NOK", "fx", 1.0, 1.5, "%", "Yahoo Finance", YQ("EURNOK=X")],
  "USDNOK=X": ["USD/NOK", "USD/NOK", "fx", 1.0, 1.5, "%", "Yahoo Finance", YQ("USDNOK=X")],
  "DX-Y.NYB": ["the dollar index", "dollarindeksen", "fx", 1.0, 1.5, "%", "Yahoo Finance", YQ("DX-Y.NYB")],
  "^GSPC": ["the S&P 500", "S&P 500", "equity", 2.0, 3.0, "%", "Yahoo Finance", YQ("^GSPC")],
  "^IXIC": ["the Nasdaq Composite", "Nasdaq Composite", "equity", 2.5, 4.0, "%", "Yahoo Finance", YQ("^IXIC")],
  "^SOX": ["the SOX semiconductor index", "halvlederindeksen SOX", "equity", 4.0, 7.0, "%", "Yahoo Finance", YQ("^SOX")],
  "^STOXX": ["the STOXX Europe 600", "STOXX Europe 600", "equity", 2.0, 3.0, "%", "Yahoo Finance", YQ("^STOXX")],
  "OSEBX.OL": ["the Oslo Børs benchmark index", "hovedindeksen på Oslo Børs", "equity", 2.0, 3.0, "%", "Yahoo Finance", YQ("OSEBX.OL")],
  "^N225": ["the Nikkei 225", "Nikkei 225", "equity", 2.5, 4.0, "%", "Yahoo Finance", YQ("^N225")],
  "^KS11": ["the KOSPI", "KOSPI", "equity", 3.0, 5.0, "%", "Yahoo Finance", YQ("^KS11")],
  "^VIX": ["the VIX", "VIX", "equity", 20.0, 35.0, "%", "Yahoo Finance", YQ("^VIX")],
  "^TNX": ["the US 10-year Treasury yield", "den amerikanske tiårsrenten", "rates", 15.0, 25.0, "bp", "Yahoo Finance", YQ("^TNX")],
  "BZ=F": ["Brent crude", "Brent-oljen", "commodity", 5.0, 8.0, "%", "Yahoo Finance", YQ("BZ=F")],
  "TTF=F": ["Dutch TTF gas", "nederlandsk TTF-gass", "commodity", 8.0, 15.0, "%", "Yahoo Finance", YQ("TTF=F")],
  "GC=F": ["gold", "gull", "commodity", 3.0, 5.0, "%", "Yahoo Finance", YQ("GC=F")],
  "HG=F": ["copper", "kobber", "commodity", 4.0, 6.0, "%", "Yahoo Finance", YQ("HG=F")],
};
const I44 = ["the import-weighted krone index I-44", "den importveide kroneindeksen I-44", "fx", 1.0, 1.5, "%", "Norges Bank", "https://www.norges-bank.no/en/topics/Statistics/exchange_rates/"];
const FEEDS = [
  ["Reuters", "https://news.google.com/rss/search?q=site:reuters.com+(markets+OR+economy+OR+%22central+bank%22)&hl=en-US&gl=US&ceid=US:en"],
  ["Bloomberg", "https://feeds.bloomberg.com/markets/news.rss"],
  ["FT", "https://www.ft.com/rss/home"],
  ["WSJ", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"],
];
const HIGH_WORDS = ["emergency", "circuit breaker", "trading halt", "halts trading", "trading halted", "market crash", "defaults on", "bankruptcy", "collapses", "invasion", "invades", "missile strike", "nuclear", "unscheduled meeting", "intervenes", "intervention in", "capital controls", "bank run", "bailout", "state of emergency", "assassinat", "coup "];
const MEDIUM_WORDS = ["plunge", "tumble", "rout", "sell-off", "selloff", "surprise rate", "unexpectedly cut", "unexpectedly raise", "unexpectedly hike", "downgrade", "sanctions on", "tariffs on", "resigns", "steps down", "crisis"];

export class AlertsStore {
  constructor(state) { this.state = state; }
  async fetch(request) {
    if (request.method === "PUT") { await this.state.storage.put("events", await request.text()); return new Response("ok"); }
    const v = await this.state.storage.get("events");
    return new Response(v || JSON.stringify({updated: null, events: []}), {headers: {"content-type": "application/json"}});
  }
}
const store = env => env.ALERTS_DO.get(env.ALERTS_DO.idFromName("main"));
const readEvents = async env => (await store(env).fetch("https://alerts/", {method: "GET"})).json();
const writeEvents = async (env, obj) => store(env).fetch("https://alerts/", {method: "PUT", body: JSON.stringify(obj)});

const json = (obj, status = 200) => new Response(JSON.stringify(obj), {status, headers: {"content-type": "application/json; charset=utf-8", "cache-control": "no-store", "access-control-allow-origin": "*"}});

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/alerts") {
      return json(await readEvents(env));
    }
    if (url.pathname === "/api/alerts/run" && url.searchParams.get("key") && env.RUN_KEY && url.searchParams.get("key") === env.RUN_KEY) {
      const r = await sniff(env);   // manual trigger for testing, protected by the RUN_KEY secret
      return json(r);
    }
    return env.ASSETS.fetch(request);
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil(sniff(env));
  },
};

async function get(url) {
  const r = await fetch(url, {headers: {"User-Agent": UA, "Accept": "application/json, application/rss+xml, text/xml, */*"}, cf: {cacheTtl: 0}});
  if (!r.ok) throw new Error(`${url.split("/")[2]} ${r.status}`);
  return r.text();
}

const num = (x, d) => Number(x).toLocaleString("en-GB", {minimumFractionDigits: d, maximumFractionDigits: d});
const numNo = (x, d) => Number(x).toLocaleString("nb-NO", {minimumFractionDigits: d, maximumFractionDigits: d});
const dateEn = iso => new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB", {day: "numeric", month: "long", year: "numeric", timeZone: "UTC"});

function makeEvent(sym, rule, last, prev, dec, dateIso, timeIso, live) {
  const [en, no, kind, med, high, unit, src, url] = rule;
  const d1 = unit === "bp" ? 100 * (last - prev) : 100 * (last / prev - 1);
  const size = Math.abs(d1);
  let sev;
  if (sym === "^VIX") { if (last < 30 && size < med) return null; sev = last >= 35 || size >= high ? "high" : "medium"; }
  else { if (size < med) return null; sev = size >= high ? "high" : "medium"; }
  const up = d1 > 0;
  const cap = s => s[0].toUpperCase() + s.slice(1);
  const chgEn = unit === "bp" ? `${size.toFixed(0)} basis points to ${last.toFixed(2)} per cent` : `${size.toFixed(1)} per cent to ${num(last, dec)}`;
  const chgNo = unit === "bp" ? `${size.toFixed(0)} basispunkter til ${numNo(last, 2)} prosent` : `${size.toFixed(1).replace(".", ",")} prosent til ${numNo(last, dec)}`;
  let textEn = `${cap(en)} ${up ? "rose" : "fell"} ${chgEn} on ${dateEn(dateIso)}${live ? " (live print, session not closed)" : ""}.`;
  let textNo = `${cap(no)} ${up ? "steg" : "falt"} ${chgNo} ${dateIso}${live ? " (løpende kurs, handelen ikke avsluttet)" : ""}.`;
  if (kind === "fx" && sym !== "DX-Y.NYB") { textEn += up ? " A rise is a weaker krone." : " A fall is a stronger krone."; textNo += up ? " Oppgang betyr svakere krone." : " Nedgang betyr sterkere krone."; }
  return {id: `${sym}:${dateIso}`, time: timeIso, severity: sev, kind, origin: "worker", source: src, url, text_en: textEn, text_no: textNo, value: {symbol: sym, last, d1: Math.round(d1 * 1000) / 1000, unit}};
}

async function yahoo(sym) {
  const j = JSON.parse(await get(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(sym)}?range=1d&interval=15m`));
  const m = j.chart.result[0].meta;
  const last = m.regularMarketPrice, prev = m.chartPreviousClose ?? m.previousClose;
  const t = new Date(m.regularMarketTime * 1000);
  // the session date in the exchange's own time zone, so the id matches the daily close used by the pipeline
  const dateIso = new Intl.DateTimeFormat("en-CA", {timeZone: m.exchangeTimezoneName || "UTC", year: "numeric", month: "2-digit", day: "2-digit"}).format(t);
  const live = !!m.currentTradingPeriod && m.regularMarketTime < (m.currentTradingPeriod.regular?.end ?? 0);
  return {last, prev, dateIso, timeIso: t.toISOString().slice(0, 19) + "Z", live, dec: /NOK=X|USD=X/.test(sym) ? 4 : sym === "^TNX" ? 2 : 2};
}

async function sniff(env) {
  const db = await readEvents(env);
  const byId = new Map(db.events.map(e => [e.id, e]));
  const report = {checked: 0, added: [], updated: [], errors: []};
  const upsert = ev => { const old = byId.get(ev.id); if (!old) { byId.set(ev.id, ev); report.added.push(ev.id); } else if (old.origin !== "agent" && (old.severity !== ev.severity || Math.abs((old.value?.d1 || 0) - (ev.value?.d1 || 0)) >= 0.5)) { byId.set(ev.id, {...old, ...ev, time: old.time}); report.updated.push(ev.id); } };
  // prices
  const results = await Promise.allSettled(Object.keys(RULES).map(async sym => { const q = await yahoo(sym); report.checked++; const ev = makeEvent(sym, RULES[sym], q.last, q.prev, q.dec, q.dateIso, q.timeIso, q.live); if (ev) upsert(ev); }));
  results.forEach((r, i) => { if (r.status === "rejected") report.errors.push(`${Object.keys(RULES)[i]}: ${String(r.reason).slice(0, 80)}`); });
  // I-44, the daily fix from Norges Bank
  try {
    const csv = await get("https://data.norges-bank.no/api/data/EXR/B.I44.NOK.SP?format=csv&lastNObservations=2");
    const rows = csv.trim().split("\n").slice(1).map(l => l.split(";")); const hdr = csv.split("\n")[0].split(";");
    const ti = hdr.indexOf("TIME_PERIOD"), vi = hdr.indexOf("OBS_VALUE");
    if (rows.length >= 2) { const prev = +rows[rows.length - 2][vi], last = +rows[rows.length - 1][vi], d = rows[rows.length - 1][ti]; const ev = makeEvent("nok_i44", I44, last, prev, 2, d, d + "T13:15:00Z", false); if (ev) upsert(ev); report.checked++; }
  } catch (e) { report.errors.push("I-44: " + String(e).slice(0, 80)); }
  // headlines
  for (const [src, url] of FEEDS) {
    try {
      const xml = await get(url);
      const items = [...xml.matchAll(/<item>([\s\S]*?)<\/item>/g)].slice(0, 40);
      let n = 0;
      for (const m of items) {
        const it = m[1];
        const title = (it.match(/<title>(?:<!\[CDATA\[)?([\s\S]*?)(?:\]\]>)?<\/title>/) || [])[1]?.trim().replace(/\s+-\s+[^-]{2,40}$/, "") || "";
        const link = (it.match(/<link>([\s\S]*?)<\/link>/) || [])[1]?.trim() || "";
        const pub = (it.match(/<pubDate>([\s\S]*?)<\/pubDate>/) || [])[1]?.trim();
        if (!title || !link) continue;
        const low = " " + title.toLowerCase() + " ";
        const hit = w => { const k = low.indexOf(w); return k >= 0 && !/[a-z]/.test(low[k - 1] || " ") && !/[a-z]/.test(low[k + w.length] || " "); };   // whole words only ("stumble" is not "tumble")
        const high = HIGH_WORDS.some(hit), med = MEDIUM_WORDS.some(hit);
        if (!high && !med) continue;
        const when = pub ? new Date(pub) : new Date();
        if (isNaN(when) || Date.now() - when.getTime() > 36 * 3600e3) continue;
        const id = "feed:" + await shortHash(link);
        if (byId.has(id)) continue;
        byId.set(id, {id, time: when.toISOString().slice(0, 19) + "Z", severity: high ? "high" : "medium", kind: "shock", origin: "worker", source: src, url: link, text_en: title, text_no: title, headline: true});
        report.added.push(id); if (++n >= 3) break;
      }
    } catch (e) { report.errors.push(src + ": " + String(e).slice(0, 80)); }
  }
  const events = [...byId.values()].sort((a, b) => b.time.localeCompare(a.time)).slice(0, KEEP);
  await writeEvents(env, {updated: new Date().toISOString().slice(0, 19) + "Z", events, last_report: report});
  return report;
}

async function shortHash(s) {
  const buf = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].slice(0, 6).map(b => b.toString(16).padStart(2, "0")).join("");
}
