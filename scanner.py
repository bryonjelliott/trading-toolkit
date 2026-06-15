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
import sys
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo

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
    rows.sort(key=lambda r: (-r["passed_eval"], r["ticker"]))

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
