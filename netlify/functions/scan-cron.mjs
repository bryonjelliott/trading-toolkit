/**
 * Scheduled scan — runs on a cron and refreshes the Supabase cache.
 *
 * Every device reads the cached result from Supabase instantly; this function
 * is the only thing that talks to Yahoo, so users never hit rate limits or the
 * 10s function timeout.
 *
 * Schedule: every 15 minutes. Adjust the cron below if you want it tighter.
 * (Yahoo daily bars only change a little intraday, so 15 min is plenty live.)
 */
import { runScan, writeCache } from "./scan.mjs";

export const config = {
  schedule: "*/15 * * * *",
};

export default async () => {
  const payload = await runScan();

  // Only overwrite the cache if we got a healthy result — a partial fetch
  // (e.g. transient throttling) shouldn't wipe the last good scan.
  if (payload.rows.length >= 40) {
    const cache = await writeCache(payload);
    console.log(`scan-cron: ${payload.rows.length} rows, market=${payload.market}, cached=${cache.cached}`);
  } else {
    console.log(`scan-cron: only ${payload.rows.length} rows — skipping cache write (kept previous).`);
  }

  return new Response("ok");
};
