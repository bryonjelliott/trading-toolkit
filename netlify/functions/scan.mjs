/**
 * Confluence Scanner — Netlify serverless function (Node port of scanner.py)
 *
 * Fetches live daily data from Yahoo for the watchlist + SPY/QQQ, computes
 * EMA 9/21, RSI 14 (Wilder), volume ratio, and market trend, then returns the
 * scored rows as JSON. Math mirrors indicators.py so results match the Python CLI.
 *
 * Local test:  node netlify/functions/scan.mjs --local
 * Production:  GET /api/scan  (redirected to /.netlify/functions/scan)
 */
import yahooFinance from "yahoo-finance2";
import { createClient } from "@supabase/supabase-js";

// Quiet the library's startup notices in serverless logs.
yahooFinance.suppressNotices(["yahooSurvey", "ripHistorical"]);

// ---------------------------------------------------------------------------
// Config (mirrors config.py — the canonical copy for the deployed app)
// ---------------------------------------------------------------------------
const CFG = {
  RSI_MIN: 28, RSI_MAX: 74,
  VOLUME_MULTIPLIER: 1.1,
  EMA_SHORT: 9, EMA_LONG: 21,
  RSI_PERIOD: 14, VOL_AVG_PERIOD: 20,
  MIN_SIGNALS: 3,
  PRICE_MIN: 4.0, PRICE_MAX: 250.0,
  BENCHMARKS: ["SPY", "QQQ"],
};

const WATCHLIST = [
  "AAPL","AMD","NVDA","INTC","MU","AMAT","QCOM","AVGO","TXN","ASML",
  "GOOGL","AMZN","CSCO","ADBE","CRM","ORCL","PYPL","SHOP","UBER",
  "MRVL","ON","SWKS","WDC","STX","ARM","SMCI","WOLF","LSCC",
  "PLTR","SNAP","PINS","ROKU","DDOG","NET","CRWD","ZS","OKTA",
  "DOCU","TWLO","U","RBLX","AFRM","SOFI","HOOD","COIN",
  "TSLA","RIVN","LCID","NIO","PLUG","FCEL","CHPT","RUN","ENPH",
  "MRNA","GILD","BIIB","VRTX","ALNY","INCY","NVAX","OCGN",
  "PEP","COST","SBUX","MAR","ABNB","DKNG","MARA","RIOT","CLSK",
  "AAL","LULU","WBD","PSKY","GME","AMC",
];

// ---------------------------------------------------------------------------
// Indicators (match indicators.py exactly)
// ---------------------------------------------------------------------------
export function emaLast(values, period) {
  if (!values.length) return NaN;
  const k = 2 / (period + 1);
  let e = values[0];
  for (let i = 1; i < values.length; i++) e = values[i] * k + e * (1 - k);
  return e;
}

// Wilder RSI via ewm(alpha=1/period, adjust=False) — matches pandas exactly.
export function rsiLast(closes, period = 14) {
  if (closes.length < 2) return NaN;
  const alpha = 1 / period;
  let avgGain = null, avgLoss = null;
  for (let i = 1; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    const g = d > 0 ? d : 0;
    const l = d < 0 ? -d : 0;
    if (avgGain === null) { avgGain = g; avgLoss = l; }
    else {
      avgGain = alpha * g + (1 - alpha) * avgGain;
      avgLoss = alpha * l + (1 - alpha) * avgLoss;
    }
  }
  if (avgLoss === 0) return 100;
  return 100 - 100 / (1 + avgGain / avgLoss);
}

export function volumeRatio(volumes, period = 20) {
  if (volumes.length < period + 1) return 0;
  const recent = volumes.slice(-period);
  const avg = recent.reduce((a, b) => a + b, 0) / period;
  if (avg === 0) return 0;
  return volumes[volumes.length - 1] / avg;
}

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Reject if `p` doesn't settle within `ms` — bounds a single hung Yahoo call so
// it can't blow the overall function budget.
function withTimeout(p, ms, label = "timeout") {
  return Promise.race([
    p,
    new Promise((_, rej) => setTimeout(() => rej(new Error(label)), ms)),
  ]);
}

async function fetchDaily(symbol, retries = 2) {
  // ~220 calendar days is plenty for EMA21 / RSI14 / 20d-volume warmup and is
  // much faster to fetch & parse than a full year.
  const period1 = new Date(Date.now() - 220 * 24 * 3600 * 1000);
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const r = await withTimeout(
        yahooFinance.chart(symbol, { period1, interval: "1d" }), 6000, "chart timeout"
      );
      const rows = (r?.quotes || []).filter((q) => q.close != null && q.volume != null);
      return rows.map((q) => ({ close: q.close, high: q.high, low: q.low, volume: q.volume }));
    } catch (e) {
      const msg = String(e?.message || e);
      const throttled = msg.includes("Too Many") || msg.includes("429");
      if (throttled && attempt < retries) {
        await sleep(500 * (attempt + 1) + Math.random() * 300); // backoff + jitter
        continue;
      }
      throw e;
    }
  }
  return [];
}

// Limit concurrency so we avoid throttling. `deadline` (epoch ms) caps total
// time: once passed, no new fetches start — we return whatever we collected so
// the function never hits the hard 30s sandbox timeout.
async function mapPool(items, limit, worker, deadline) {
  const out = new Array(items.length);
  let i = 0;
  const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (i < items.length) {
      if (deadline && Date.now() > deadline) return;
      const idx = i++;
      try { out[idx] = await worker(items[idx]); }
      catch (e) { out[idx] = { __error: String(e?.message || e) }; }
    }
  });
  await Promise.all(runners);
  return out;
}

// ---------------------------------------------------------------------------
// Scoring
// ---------------------------------------------------------------------------
function marketTrend(spy, qqq) {
  const state = (bars) => {
    if (!bars || bars.length < CFG.EMA_LONG) return null;
    const closes = bars.map((b) => b.close);
    return emaLast(closes, CFG.EMA_SHORT) > emaLast(closes, CFG.EMA_LONG) ? "bull" : "bear";
  };
  const a = state(spy), b = state(qqq);
  if (a === "bull" && b === "bull") return "bull";
  if (a === "bear" && b === "bear") return "bear";
  return "mixed";
}

function evaluate(sym, bars, mkt, livePrice) {
  if (!bars || bars.length < Math.max(CFG.EMA_LONG, CFG.VOL_AVG_PERIOD) + 1) return null;
  const closes = bars.map((b) => b.close);
  const volumes = bars.map((b) => b.volume);

  const price = livePrice != null ? livePrice : closes[closes.length - 1];
  if (!(price >= CFG.PRICE_MIN && price <= CFG.PRICE_MAX)) return null;

  const e9 = emaLast(closes, CFG.EMA_SHORT);
  const e21 = emaLast(closes, CFG.EMA_LONG);
  const direction = e9 >= e21 ? "LONG" : "SHORT";

  const sig_ema = true; // direction derived from EMA alignment
  const ema_gap = e9 - e21;

  const rsi = rsiLast(closes, CFG.RSI_PERIOD);
  const sig_rsi = rsi >= CFG.RSI_MIN && rsi <= CFG.RSI_MAX;

  const vratio = volumeRatio(volumes, CFG.VOL_AVG_PERIOD);
  const sig_vol = vratio >= CFG.VOLUME_MULTIPLIER;

  const sig_mkt = direction === "LONG" ? mkt === "bull" : mkt === "bear";

  const passed_eval = [sig_ema, sig_rsi, sig_vol, sig_mkt].filter(Boolean).length;

  return {
    ticker: sym, price, direction,
    ema9: e9, ema21: e21, ema_gap, rsi, vratio,
    sig_sr: null, sig_ema, sig_rsi, sig_vol, sig_mkt,
    passed_eval,
  };
}

// ---------------------------------------------------------------------------
// Core scan
// ---------------------------------------------------------------------------
export async function runScan(budgetMs = 22000, limitN = 0) {
  const deadline = Date.now() + budgetMs;
  const list = limitN > 0 ? WATCHLIST.slice(0, limitN) : WATCHLIST;

  // Benchmarks FIRST so the market-trend signal is reliable even if the
  // deadline cuts the tail of the watchlist.
  const all = [...new Set([...CFG.BENCHMARKS, ...list])];

  // Daily history, concurrency-limited, deadline-bounded.
  const bars = {};
  const results = await mapPool(
    all, 8, async (sym) => ({ sym, data: await fetchDaily(sym) }), deadline
  );
  for (const r of results) {
    if (r && r.sym && !r.__error && r.data?.length) bars[r.sym] = r.data;
  }

  const mkt = marketTrend(bars["SPY"], bars["QQQ"]);

  const rows = [];
  for (const sym of list) {
    const res = evaluate(sym, bars[sym], mkt); // price = latest daily close
    if (res) rows.push(res);
  }
  rows.sort((a, b) => b.passed_eval - a.passed_eval || a.ticker.localeCompare(b.ticker));

  const qualifying = rows.filter((r) => r.passed_eval >= CFG.MIN_SIGNALS).length;
  return {
    market: mkt, error: null, rows,
    total: rows.length, qualifying, min_signals: CFG.MIN_SIGNALS,
    fetched: Object.keys(bars).length, watchlist: list.length,
    generated_at: new Date().toISOString(),
  };
}

// ---------------------------------------------------------------------------
// Supabase cache write (server-side; uses service key, never exposed to browser)
// ---------------------------------------------------------------------------
export async function writeCache(payload) {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_KEY || process.env.SUPABASE_ANON_KEY;
  if (!url || !key) return { cached: false, reason: "no SUPABASE_URL / key env set" };
  try {
    const sb = createClient(url, key, { auth: { persistSession: false } });
    const { error } = await sb
      .from("scan_cache")
      .upsert({ id: 1, payload, updated_at: new Date().toISOString() });
    return error ? { cached: false, reason: error.message } : { cached: true };
  } catch (e) {
    return { cached: false, reason: String(e?.message || e) };
  }
}

// ---------------------------------------------------------------------------
// Netlify handler (on-demand: GET /api/scan)
// ---------------------------------------------------------------------------
export const handler = async (event) => {
  try {
    // ?limit=N scans only the first N watchlist symbols (diagnostic / fast path).
    const limit = parseInt(event?.queryStringParameters?.limit || "0", 10) || 0;
    const payload = await runScan(22000, limit);
    const cache = await writeCache(payload);
    payload.cache = cache;
    return {
      statusCode: 200,
      headers: { "content-type": "application/json", "cache-control": "no-store" },
      body: JSON.stringify(payload),
    };
  } catch (e) {
    return {
      statusCode: 500,
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ error: String(e?.message || e), rows: [] }),
    };
  }
};

// Local CLI test: `node netlify/functions/scan.mjs --local`
if (process.argv.includes("--local")) {
  const t0 = Date.now();
  runScan().then((r) => {
    console.log(`market=${r.market}  fetched=${r.fetched}/${r.watchlist + 2}  total=${r.total}  qualifying=${r.qualifying}  (${((Date.now()-t0)/1000).toFixed(1)}s)`);
    console.log("top rows:");
    for (const row of r.rows.slice(0, 8)) {
      console.log(
        `  ${row.ticker.padEnd(6)} ${row.direction.padEnd(5)} ` +
        `eval=${row.passed_eval}/4  rsi=${row.rsi.toFixed(1)}  vol=${row.vratio.toFixed(2)}  ` +
        `price=${row.price.toFixed(2)}`
      );
    }
  }).catch((e) => { console.error("ERROR:", e); process.exit(1); });
}
