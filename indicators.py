"""
Pure indicator functions. All take a pandas Series/DataFrame and return values.
Kept dependency-free of config so they can be unit-tested in isolation.
"""
from __future__ import annotations
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (standard, adjust=False)."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder's smoothing == EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    out = 100 - (100 / (1 + rs))
    # When avg_loss == 0, RSI is 100 (avoid NaN/inf)
    out = out.where(avg_loss != 0, 100.0)
    return out


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range using Wilder's smoothing."""
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def volume_ratio(volume: pd.Series, period: int = 20) -> float:
    """
    Ratio of the most recent completed session's volume to the trailing
    `period`-session average volume. Returns 0.0 if insufficient data.
    """
    if len(volume) < period + 1:
        return 0.0
    avg = volume.iloc[-period:].mean()
    if avg == 0:
        return 0.0
    return float(volume.iloc[-1] / avg)


def last(series: pd.Series) -> float:
    """Last non-NaN value of a series as a float (NaN-safe)."""
    s = series.dropna()
    return float(s.iloc[-1]) if len(s) else float("nan")
