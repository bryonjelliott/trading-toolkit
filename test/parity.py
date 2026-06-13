import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from indicators import ema, rsi, volume_ratio, last

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "parity_data.json")) as f:
    d = json.load(f)

closes = pd.Series(d["closes"], dtype="float64")
volumes = pd.Series(d["volumes"], dtype="float64")

print(json.dumps({
    "ema9": last(ema(closes, 9)),
    "ema21": last(ema(closes, 21)),
    "rsi14": last(rsi(closes, 14)),
    "vol_ratio": volume_ratio(volumes, 20),
}, indent=2))
