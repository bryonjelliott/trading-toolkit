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
import sys
import warnings

# Ensure UTF-8 / clean output on Windows consoles
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd
import yfinance as yf

import config as C
from indicators import ema, rsi, volume_ratio, last

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

    passed = sum([sig_ema, sig_rsi, sig_vol, sig_mkt])

    return {
        "ticker": sym,
        "price": price,
        "direction": direction,
        "ema9": e9,
        "ema21": e21,
        "ema_gap": ema_gap,
        "rsi": rsi_val,
        "vratio": vratio,
        "sig_sr": None,       # Stage 2
        "sig_ema": sig_ema,
        "sig_rsi": sig_rsi,
        "sig_vol": sig_vol,
        "sig_mkt": sig_mkt,
        "passed_eval": passed,  # out of 4 evaluated signals this stage
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
    print(f"  CONFLUENCE SCANNER - STAGE 1   |   Market trend (SPY+QQQ): {mkt.upper()}")
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
            f"{r['passed_eval']:>4}/4"
        )
    print("-" * 96)
    qualifying = [r for r in rows if r["passed_eval"] >= C.MIN_SIGNALS]
    print(f"  {len(rows)} tickers in price band | "
          f"{len(qualifying)} with >= {C.MIN_SIGNALS} signals "
          f"(of 4 evaluated; S/R signal arrives in Stage 2)")
    print("=" * 96)
    print("  Legend: PASS = signal passed,  fail = failed,   - = not yet evaluated (Stage 2)")
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
