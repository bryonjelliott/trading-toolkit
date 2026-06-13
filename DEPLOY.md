# Deploy guide — Trading Toolkit on Netlify + Supabase

This hosts the whole app (landing page, scanner, journal) on **Netlify** for free,
accessible from any device, with **Supabase** storing your trades and the cached scan.

There are three things to set up, in order:

1. **Supabase** — the database (5 min)
2. **Netlify** — host the site + run the scanner function (5 min)
3. **Wire the keys together** (2 min)

---

## 1. Supabase

1. Create a free account at <https://supabase.com> and make a **New project**.
   Pick a strong database password (you won't need it again here).
2. When the project is ready, open **SQL Editor → New query**, paste the entire
   contents of [`supabase_schema.sql`](supabase_schema.sql), and click **Run**.
   This creates the `trades` and `scan_cache` tables and their access policies.
3. Open **Project Settings → API** and copy these three values — you'll need them:
   - **Project URL**            (e.g. `https://abcd1234.supabase.co`)
   - **anon / public** key      (safe to expose in the browser)
   - **service_role** key       (SECRET — only goes in Netlify, never in the frontend)

---

## 2. Netlify

The easiest path is **Git-based deploy** (auto-redeploys when you push changes).

### Option A — Git (recommended)
1. Put this project in a GitHub repo (see "Push to GitHub" below).
2. At <https://app.netlify.com> → **Add new site → Import an existing project**.
3. Choose your repo. Netlify reads [`netlify.toml`](netlify.toml) automatically:
   - Publish directory: `web`
   - Functions directory: `netlify/functions`
   No build command needed.
4. Click **Deploy**.

### Option B — Drag & drop (quick test, no functions/scheduling)
Drag the `web/` folder onto <https://app.netlify.com/drop>. This serves the
static pages but **not** the scanner function — use Option A for the full app.

---

## 3. Wire the keys together

### a) Frontend key (in the repo)
Edit [`web/assets/config.js`](web/assets/config.js) and replace the placeholders:
```js
window.APP_CONFIG = {
  SUPABASE_URL: "https://YOUR-PROJECT.supabase.co",   // Project URL
  SUPABASE_ANON_KEY: "eyJhbGci...",                    // anon / public key
};
```
Commit & push (Netlify redeploys).

### b) Function keys (in Netlify dashboard)
Netlify site → **Site configuration → Environment variables → Add a variable**:

| Key                    | Value                          |
|------------------------|--------------------------------|
| `SUPABASE_URL`         | your Project URL               |
| `SUPABASE_SERVICE_KEY` | your **service_role** key      |

Redeploy (Deploys → Trigger deploy) so the function picks up the variables.

---

## How it runs

- **Scheduled function** [`scan-cron.mjs`](netlify/functions/scan-cron.mjs) runs every
  15 minutes, scans Yahoo, and writes the result to `scan_cache` in Supabase.
- The **scanner page** reads that cache from Supabase — instant on every device,
  no rate limits. "Refresh now" can also trigger a live scan on demand.
- The **journal** reads/writes the `trades` table directly from the browser.

To seed the first scan immediately (instead of waiting 15 min), open the scanner
page once it's deployed and click **Refresh now**, or visit `/api/scan` directly.

### Adjust things later
- **Watchlist / thresholds:** edit the `WATCHLIST` and `CFG` blocks at the top of
  [`netlify/functions/scan.mjs`](netlify/functions/scan.mjs). (The Python
  `config.py` mirrors these for the local CLI.)
- **Scan frequency:** change the cron in [`scan-cron.mjs`](netlify/functions/scan-cron.mjs).

---

## Push to GitHub
From the project folder:
```bash
git init
git add .
git commit -m "Trading Toolkit: scanner + journal"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

---

## Security note
You chose **no login / single shared space**. Anyone who has your site URL can
view and edit the journal (the anon key in the browser allows it, by design).
Keep the URL private. If you later want private, per-user data, enable Supabase
Auth and tighten the RLS policies in `supabase_schema.sql` to `auth.uid()`.
