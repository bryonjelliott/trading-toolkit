"""
Run the confluence scan and write the result to Supabase `scan_cache`.

Designed to run in GitHub Actions (where Yahoo Finance is reachable, unlike
Netlify's datacenter IPs). The website reads `scan_cache` from Supabase, so
this is what keeps the live scanner populated.

Env vars required:
  SUPABASE_URL          e.g. https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  service_role key (server-side; bypasses RLS)
"""
import json
import math
import os
import sys
from datetime import datetime, timezone

import requests

from scanner import run_scan
import config as C

MIN_ROWS_TO_WRITE = 20  # don't overwrite a good cache with a throttled/partial scan


def clean(o):
    """Replace NaN/Inf with None so the JSON is valid for Postgres jsonb."""
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


def main() -> int:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.")
        return 1

    print("Running scan ...", flush=True)
    result = run_scan()
    if result.get("error"):
        print("Scan error:", result["error"])
        return 1

    rows = result["rows"]
    qualifying = sum(1 for r in rows if r["passed_eval"] >= C.MIN_SIGNALS)
    payload = clean({
        "market": result["market"],
        "error": None,
        "rows": rows,
        "total": len(rows),
        "qualifying": qualifying,
        "min_signals": C.MIN_SIGNALS,
        "source": "github-actions",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })

    print(f"Scan: market={payload['market']} total={payload['total']} qualifying={qualifying}")
    if len(rows) < MIN_ROWS_TO_WRITE:
        print(f"Only {len(rows)} rows (< {MIN_ROWS_TO_WRITE}) — likely throttled. "
              f"NOT writing cache (keeping previous).")
        return 1

    endpoint = url.rstrip("/") + "/rest/v1/scan_cache?on_conflict=id"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    body = [{"id": 1, "payload": payload, "updated_at": payload["generated_at"]}]
    resp = requests.post(endpoint, headers=headers, data=json.dumps(body), timeout=30)
    print("Supabase upsert ->", resp.status_code, resp.text[:200])
    resp.raise_for_status()
    print("Cache updated successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
