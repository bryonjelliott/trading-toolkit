"""
Confluence Day Trading Scanner — STAGE 1
========================================
Fetches 1y daily OHLCV for the watchlist + benchmarks, computes:
  - Signal 2: EMA 9/21 alignment (trend direction)
  - Signal 3: RSI 14 filter
  - Signal 4: Volume confirmation (yesterday vol vs 20-period avg)
  - Signal 5: SPY/QQQ trend alignment (shared market signal)

Signal 1 (Support/Resistance proximity) is added in Stage 2, so each ticker
is currently scored out of 4 evaluated signals (shown as "x/4 eval").

Run:  py -3.11 scanner.py
"""
from __future__ import annotations
import math
import os
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY")

# Ensure UTF-8 / clean output on Windows consoles
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd
import yfinance as yf

import config as C
from indicators import ema, rsi, atr, volume_ratio, last

STOP_ATR_MULT = 0.75   # stop sits 0.75 x ATR from entry (Stage 2 default)
ENTRY_BUFFER = 0.05    # breakout/breakdown trigger offset (spec: $0.05)


def _prior_session(df):
    """Last *completed* daily bar — skip today's still-forming bar if present."""
    try:
        last_date = df.index[-1].date()
        today = datetime.now(ZoneInfo("America/New_York")).date()
        if last_date == today and len(df) >= 2:
            return df.iloc[-2]
    except Exception:
        pass
    return df.iloc[-1]


def sr_levels(df, lookback=252, swing=5, cluster_pct=0.01, min_touches=2):
    """
    Horizontal support/resistance levels: swing-pivot highs/lows (a bar that is
    the extreme of a +/- `swing` window) clustered within `cluster_pct`; a cluster
    of >= `min_touches` pivots becomes a level (price reversed there repeatedly).
    """
    data = df.iloc[-lookback:]
    highs = [float(x) for x in data["High"].values]
    lows = [float(x) for x in data["Low"].values]
    n = len(highs)
    pivots = []
    for i in range(swing, n - swing):
        if highs[i] >= max(highs[i - swing:i + swing + 1]):
            pivots.append(highs[i])
        if lows[i] <= min(lows[i - swing:i + swing + 1]):
            pivots.append(lows[i])
    pivots = sorted(p for p in pivots if p == p and p > 0)  # drop NaN/zero

    levels, used = [], [False] * len(pivots)
    for i, p in enumerate(pivots):
        if used[i]:
            continue
        cluster = [p]
        used[i] = True
        for j in range(i + 1, len(pivots)):
            if (pivots[j] - p) / p <= cluster_pct:
                cluster.append(pivots[j])
                used[j] = True
            else:
                break  # sorted: nothing further is in range
        if len(cluster) >= min_touches:
            levels.append(sum(cluster) / len(cluster))
    return levels


def sr_signal(levels, price, direction, proximity=C.SR_PROXIMITY_PCT):
    """(passed, nearest_level): long needs an active support, short an active resistance."""
    if not levels or not price:
        return False, None
    if direction == "LONG":
        cand = [L for L in levels if L <= price and (price - L) / price <= proximity]
        return (True, max(cand)) if cand else (False, None)
    cand = [L for L in levels if L >= price and (L - price) / price <= proximity]
    return (True, min(cand)) if cand else (False, None)


def build_setup(df, direction: str, sr_level=None):
    """Entry / stop / targets. Stop = nearest S/R beyond entry or 0.75x ATR,
    whichever is closer to entry (per spec); flag if outside 0.5-1.5x ATR."""
    atr_val = last(atr(df["High"], df["Low"], df["Close"], C.ATR_PERIOD))
    if atr_val is None or math.isnan(atr_val) or atr_val <= 0:
        return None

    prior = _prior_session(df)
    ph, pl = float(prior["High"]), float(prior["Low"])
    atr_dist = STOP_ATR_MULT * atr_val

    if direction == "LONG":
        entry = ph + ENTRY_BUFFER
        stop, basis = entry - atr_dist, "ATR"
        if sr_level is not None and sr_level < entry:
            sr_stop = sr_level * (1 - 0.001)         # just below support
            if sr_stop > stop:                       # closer to entry = tighter
                stop, basis = sr_stop, "S/R"
        dist = entry - stop
        t1, t2 = entry + dist, entry + 2 * dist
    else:
        entry = pl - ENTRY_BUFFER
        stop, basis = entry + atr_dist, "ATR"
        if sr_level is not None and sr_level > entry:
            sr_stop = sr_level * (1 + 0.001)         # just above resistance
            if sr_stop < stop:
                stop, basis = sr_stop, "S/R"
        dist = stop - entry
        t1, t2 = entry - dist, entry - 2 * dist

    abnormal = not (0.5 * atr_val <= dist <= 1.5 * atr_val)
    return {
        "atr": round(atr_val, 4),
        "prior_high": round(ph, 2),
        "prior_low": round(pl, 2),
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "stop_dist": round(dist, 4),
        "t1": round(t1, 2),
        "t2": round(t2, 2),
        "sr_level": round(sr_level, 2) if sr_level is not None else None,
        "stop_basis": basis,
        "abnormal": abnormal,
    }

warnings.simplefilter("ignore", category=FutureWarning)

CHECK, CROSS, DASH = "PASS", "fail", "  - "


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def fetch_all(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Batch-download 1y daily OHLCV. Returns {symbol: DataFrame}."""
    print(f"Fetching 1y daily data for {len(symbols)} symbols ...", flush=True)
    raw = yf.download(
        tickers=symbols,
        period="1y",
        interval="1d",
        auto_adjust=False,
        group_by="ticker",
        threads=True,
        progress=False,
    )
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            df = raw[sym].dropna(how="all") if len(symbols) > 1 else raw
        except (KeyError, TypeError):
            continue
        if df is None or df.empty or "Close" not in df:
            continue
        out[sym] = df
    return out


# ---------------------------------------------------------------------------
# Signal 5 — shared market trend (SPY + QQQ EMA 9/21)
# ---------------------------------------------------------------------------
def market_trend(data: dict[str, pd.DataFrame]) -> str:
    """Return 'bull', 'bear', or 'mixed' from SPY/QQQ EMA alignment."""
    states = []
    for sym in C.BENCHMARKS:
        df = data.get(sym)
        if df is None or len(df) < C.EMA_LONG:
            return "mixed"
        e9 = last(ema(df["Close"], C.EMA_SHORT))
        e21 = last(ema(df["Close"], C.EMA_LONG))
        states.append("bull" if e9 > e21 else "bear")
    if all(s == "bull" for s in states):
        return "bull"
    if all(s == "bear" for s in states):
        return "bear"
    return "mixed"


# ---------------------------------------------------------------------------
# Per-ticker signal evaluation (Stage 1: signals 2,3,4,5)
# ---------------------------------------------------------------------------
def evaluate(sym: str, df: pd.DataFrame, mkt: str) -> dict | None:
    if df is None or len(df) < max(C.EMA_LONG, C.VOL_AVG_PERIOD) + 1:
        return None

    price = last(df["Close"])
    if not (C.PRICE_MIN <= price <= C.PRICE_MAX):
        return None  # outside tradeable price band

    e9 = last(ema(df["Close"], C.EMA_SHORT))
    e21 = last(ema(df["Close"], C.EMA_LONG))
    direction = "LONG" if e9 >= e21 else "SHORT"

    # Signal 1 — price within 1.5% of an active support (long) / resistance (short)
    levels = sr_levels(df)
    sig_sr, sr_level = sr_signal(levels, price, direction)

    # Signal 2 — EMA alignment matches direction (always true by construction,
    # but we record the gap strength for later use)
    ema_gap = e9 - e21
    sig_ema = True  # direction is derived from EMA, so alignment passes

    # Signal 3 — RSI filter
    rsi_val = last(rsi(df["Close"], C.RSI_PERIOD))
    sig_rsi = C.RSI_MIN <= rsi_val <= C.RSI_MAX

    # Signal 4 — volume confirmation
    vratio = volume_ratio(df["Volume"], C.VOL_AVG_PERIOD)
    sig_vol = vratio >= C.VOLUME_MULTIPLIER

    # Signal 5 — market trend alignment
    if direction == "LONG":
        sig_mkt = (mkt == "bull")
    else:
        sig_mkt = (mkt == "bear")

    passed = sum([sig_sr, sig_ema, sig_rsi, sig_vol, sig_mkt])

    return {
        "ticker": sym,
        "price": price,
        "direction": direction,
        "ema9": e9,
        "ema21": e21,
        "ema_gap": ema_gap,
        "rsi": rsi_val,
        "vratio": vratio,
        "sig_sr": sig_sr,
        "sig_ema": sig_ema,
        "sig_rsi": sig_rsi,
        "sig_vol": sig_vol,
        "sig_mkt": sig_mkt,
        "passed_eval": passed,  # out of 5 signals
        "setup": build_setup(df, direction, sr_level),  # entry/stop/targets/ATR/S-R
        "prev_close": round(float(_prior_session(df)["Close"]), 2),
        "avg_vol": float(df["Volume"].iloc[-C.VOL_AVG_PERIOD:].mean()),
        "pm": None,  # filled by enrich_premarket()
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def mark(b) -> str:
    return CHECK if b is True else (DASH if b is None else CROSS)


def print_table(rows: list[dict], mkt: str) -> None:
    rows.sort(key=lambda r: (-r["passed_eval"], r["ticker"]))

    print()
    print("=" * 96)
    print(f"  CONFLUENCE SCANNER   |   Market trend (SPY+QQQ): {mkt.upper()}")
    print("=" * 96)
    hdr = (f"{'TICK':<6}{'PRICE':>9}{'DIR':>6}   "
           f"{'S/R':>5}{'EMA':>6}{'RSI':>6}{'VOL':>6}{'MKT':>6}   "
           f"{'RSIval':>7}{'Vol x':>7}{'EMAgap':>8}   {'EVAL':>6}")
    print(hdr)
    print("-" * 96)
    for r in rows:
        print(
            f"{r['ticker']:<6}"
            f"{r['price']:>9.2f}"
            f"{r['direction']:>6}   "
            f"{mark(r['sig_sr']):>5}"
            f"{mark(r['sig_ema']):>6}"
            f"{mark(r['sig_rsi']):>6}"
            f"{mark(r['sig_vol']):>6}"
            f"{mark(r['sig_mkt']):>6}   "
            f"{r['rsi']:>7.1f}"
            f"{r['vratio']:>7.2f}"
            f"{r['ema_gap']:>8.2f}   "
            f"{r['passed_eval']:>4}/5"
        )
    print("-" * 96)
    qualifying = [r for r in rows if r["passed_eval"] >= C.MIN_SIGNALS]
    print(f"  {len(rows)} tickers in price band | "
          f"{len(qualifying)} with >= {C.MIN_SIGNALS} of 5 signals")
    print("=" * 96)
    print("  Legend: PASS = signal passed,  fail = failed")
    print()


# ---------------------------------------------------------------------------
# Premarket context (gap %, premarket volume, float, market cap)
# ---------------------------------------------------------------------------
def _premarket_stats(intr, sym):
    """(pm_volume, pm_price) from today's 04:00-09:30 ET 1-minute bars."""
    try:
        sub = intr[sym] if isinstance(intr.columns, pd.MultiIndex) else intr
    except Exception:
        return None, None
    if sub is None or sub.empty:
        return None, None
    sub = sub.dropna(how="all")
    try:
        et = sub.index.tz_convert(ET)
    except Exception:
        try:
            et = sub.index.tz_localize("UTC").tz_convert(ET)
        except Exception:
            return None, None
    mask = [(dtime(4, 0) <= t.time() < dtime(9, 30)) for t in et]
    pm = sub[pd.Series(mask, index=sub.index)]
    if not pm.empty:
        closes = pm["Close"].dropna()
        return float(pm["Volume"].fillna(0).sum()), (float(closes.iloc[-1]) if len(closes) else None)
    closes = sub["Close"].dropna()           # weekend/holiday: no premarket bars
    return None, (float(closes.iloc[-1]) if len(closes) else None)


def _fetch_info(sym):
    try:
        info = yf.Ticker(sym).get_info() or {}
        return sym, info.get("floatShares"), info.get("marketCap")
    except Exception:
        return sym, None, None


def _cap_category(mc):
    if not mc:
        return None
    if mc < 300e6:
        return "Micro"
    if mc < 2e9:
        return "Small"
    if mc < 10e9:
        return "Mid"
    return "Large"


def enrich_premarket(rows: list[dict]) -> None:
    """Attach a `pm` dict to each row: gap %, premarket volume, float, cap. In-place."""
    if not rows:
        return
    syms = [r["ticker"] for r in rows]

    try:
        intr = yf.download(syms, period="1d", interval="1m", prepost=True,
                           group_by="ticker", threads=True, progress=False)
    except Exception:
        intr = None

    # float / market cap only for qualifiers (.info is slow); threaded.
    qual = [r["ticker"] for r in rows if r["passed_eval"] >= C.MIN_SIGNALS]
    info_map = {}
    if qual:
        try:
            with ThreadPoolExecutor(max_workers=8) as ex:
                for sym, fl, mc in ex.map(_fetch_info, qual):
                    info_map[sym] = (fl, mc)
        except Exception:
            pass

    for r in rows:
        pm_vol, pm_price = (None, None)
        if intr is not None:
            pm_vol, pm_price = _premarket_stats(intr, r["ticker"])
        prev_close, avg_vol = r.get("prev_close"), r.get("avg_vol")
        gap_pct = ((pm_price - prev_close) / prev_close * 100) if (pm_price and prev_close) else None
        pm_vol_pct = (pm_vol / avg_vol * 100) if (pm_vol and avg_vol) else None
        fl, mc = info_map.get(r["ticker"], (None, None))
        r["pm"] = {
            "gap_pct": round(gap_pct, 2) if gap_pct is not None else None,
            "pm_price": round(pm_price, 2) if pm_price else None,
            "prev_close": round(prev_close, 2) if prev_close else None,
            "pm_vol": int(pm_vol) if pm_vol else None,
            "pm_vol_pct": round(pm_vol_pct, 1) if pm_vol_pct is not None else None,
            "strong_gap": bool(gap_pct is not None and abs(gap_pct) >= C.GAP_STRONG_THRESHOLD * 100),
            "weak_gap": bool(gap_pct is not None and abs(gap_pct) <= 1.0),
            "high_pm_vol": bool(pm_vol_pct is not None and pm_vol_pct >= C.PREMARKET_VOL_THRESHOLD * 100),
            "float": int(fl) if fl else None,
            "low_float": bool(fl is not None and fl < C.LOW_FLOAT_THRESHOLD),
            "market_cap": int(mc) if mc else None,
            "cap_cat": _cap_category(mc),
        }


# ---------------------------------------------------------------------------
# Catalysts / news (Finnhub company-news)
# ---------------------------------------------------------------------------
def _fetch_news(sym, days=7, limit=3):
    """Most-recent company-news headlines from Finnhub (empty if no key/error)."""
    if not FINNHUB_KEY:
        return sym, []
    to = datetime.now(ET).date()
    frm = to - timedelta(days=days)
    url = ("https://finnhub.io/api/v1/company-news"
           f"?symbol={sym}&from={frm}&to={to}&token={FINNHUB_KEY}")
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return sym, []
        items = r.json() or []
        items.sort(key=lambda x: x.get("datetime", 0), reverse=True)
        out = []
        for it in items[:limit]:
            head = (it.get("headline") or "").strip()
            if not head:
                continue
            out.append({
                "headline": head,
                "source": it.get("source"),
                "url": it.get("url"),
                "datetime": it.get("datetime"),
                "summary": (it.get("summary") or "").strip()[:240],
            })
        return sym, out
    except Exception:
        return sym, []


def enrich_news(rows: list[dict]) -> None:
    """Attach `news` (list of recent headlines) to each qualifying row. In-place."""
    for r in rows:
        r["news"] = []
    if not FINNHUB_KEY:
        return
    qual = [r for r in rows if r["passed_eval"] >= C.MIN_SIGNALS]
    if not qual:
        return
    by_sym = {}
    try:
        with ThreadPoolExecutor(max_workers=5) as ex:
            for sym, news in ex.map(lambda r: _fetch_news(r["ticker"]), qual):
                by_sym[sym] = news
    except Exception:
        pass
    for r in qual:
        r["news"] = by_sym.get(r["ticker"], [])


# ---------------------------------------------------------------------------
def run_scan() -> dict:
    """
    Run a full scan and return structured results (shared by CLI + web API).
    Returns: {"market": str, "rows": list[dict], "error": str | None}
    """
    symbols = list(dict.fromkeys(C.WATCHLIST + C.BENCHMARKS))  # dedupe, keep order
    data = fetch_all(symbols)
    if not data:
        return {"market": "mixed", "rows": [],
                "error": "No data returned. Check your connection / ticker symbols."}

    mkt = market_trend(data)

    rows = []
    for sym in C.WATCHLIST:
        res = evaluate(sym, data.get(sym), mkt)
        if res:
            rows.append(res)

    enrich_premarket(rows)
    enrich_news(rows)

    # Sort: most signals first, then biggest gap (nulls last) — per spec priority.
    def _key(r):
        g = r["pm"]["gap_pct"] if r.get("pm") and r["pm"]["gap_pct"] is not None else None
        return (-r["passed_eval"], -(abs(g) if g is not None else -1))
    rows.sort(key=_key)

    return {"market": mkt, "rows": rows, "error": None}


def main() -> int:
    result = run_scan()
    if result["error"]:
        print("ERROR:", result["error"])
        return 1
    if not result["rows"]:
        print("No tickers passed the price-band filter / had sufficient history.")
        return 0

    print_table(result["rows"], result["market"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
