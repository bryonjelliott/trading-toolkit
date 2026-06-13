import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { emaLast, rsiLast, volumeRatio } from "../netlify/functions/scan.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const d = JSON.parse(readFileSync(join(here, "parity_data.json"), "utf8"));

console.log(JSON.stringify({
  ema9: emaLast(d.closes, 9),
  ema21: emaLast(d.closes, 21),
  rsi14: rsiLast(d.closes, 14),
  vol_ratio: volumeRatio(d.volumes, 20),
}, null, 2));
