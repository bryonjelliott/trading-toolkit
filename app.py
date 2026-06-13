"""
Flask web app for the Confluence Day Trading Scanner.

Routes
------
  /            -> landing page (hub of tool tiles)
  /scanner     -> the Confluence Scanner UI
  /api/scan    -> JSON: runs a live scan and returns market trend + rows
  /api/config  -> JSON: surfaces the key config values for display

Run:  py -3.11 app.py   then open http://127.0.0.1:5000
"""
from __future__ import annotations
import math

from flask import Flask, jsonify, render_template

import config as C
import scanner

app = Flask(__name__)


def _clean(obj):
    """Recursively replace NaN/Inf floats with None so the JSON is valid."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/scanner")
def scanner_page():
    return render_template("scanner.html")


@app.route("/api/scan")
def api_scan():
    result = scanner.run_scan()
    qualifying = [r for r in result["rows"] if r["passed_eval"] >= C.MIN_SIGNALS]
    payload = {
        "market": result["market"],
        "error": result["error"],
        "rows": result["rows"],
        "total": len(result["rows"]),
        "qualifying": len(qualifying),
        "min_signals": C.MIN_SIGNALS,
    }
    return jsonify(_clean(payload))


@app.route("/api/config")
def api_config():
    return jsonify({
        "account_balance": C.ACCOUNT_BALANCE,
        "risk_percent": C.RISK_PERCENT,
        "min_signals": C.MIN_SIGNALS,
        "rsi_min": C.RSI_MIN,
        "rsi_max": C.RSI_MAX,
        "volume_multiplier": C.VOLUME_MULTIPLIER,
        "ema_short": C.EMA_SHORT,
        "ema_long": C.EMA_LONG,
        "price_min": C.PRICE_MIN,
        "price_max": C.PRICE_MAX,
        "watchlist_size": len(C.WATCHLIST),
        "benchmarks": C.BENCHMARKS,
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
