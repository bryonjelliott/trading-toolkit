# Confluence Day Trading Scanner

Scans a ~70-ticker Nasdaq watchlist for stocks meeting at least 3 of 5
confluence signals, then builds a trade setup (entry / stop / two targets /
position size on a 1% risk rule).

## Run

```powershell
py -3.11 scanner.py
```

## Files

| File | Purpose |
|------|---------|
| `config.py` | All tunable constants + the editable watchlist. **Start here.** |
| `indicators.py` | Pure indicator functions: EMA, RSI (Wilder), ATR, volume ratio. |
| `scanner.py` | Main scan logic + terminal output. |
| `requirements.txt` | Dependencies (already installed in your env). |

## The 5 confluence signals

1. **S/R proximity** — price within 1.5% of a key support (long) / resistance (short) level. *(Stage 2)*
2. **EMA 9/21 alignment** — EMA9 > EMA21 = long bias, EMA9 < EMA21 = short bias.
3. **RSI 14 filter** — passes when 28 ≤ RSI ≤ 74 (not over-extended).
4. **Volume confirmation** — yesterday's volume ≥ 1.1× the 20-day average.
5. **SPY/QQQ alignment** — both benchmarks' EMA9/21 agree with trade direction (shared signal).

A stock needs **≥ 3 passes** to generate a setup card.

## Build stages

- [x] **Stage 1** — daily fetch, signals 2/3/4/5, terminal pass/fail table.
- [ ] **Stage 2** — support/resistance detection (signal 1), ATR, position-size calc.
- [ ] **Stage 3** — premarket data (gap %, premarket volume, RVOL, float).
- [ ] **Stage 4** — Flask dark-themed trade cards, SSE scan progress, sort/filter.
- [ ] **Stage 5** — scheduling (9:00 & 9:25 AM ET), holiday detection, config-driven watchlist.

## Notes

- Data source is `yfinance`. Signal 2 currently *defines* trade direction, so it
  always passes by construction — it's still displayed for transparency and its
  EMA gap (trend strength) is recorded for later ranking.
- Score is shown as `x/4` this stage; it becomes `x/5` once the S/R signal lands in Stage 2.
