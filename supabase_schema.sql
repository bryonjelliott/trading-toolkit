-- =====================================================================
--  Trading Toolkit — Supabase schema
--  Run this once in: Supabase dashboard -> SQL Editor -> New query -> Run
-- =====================================================================

-- ---------------------------------------------------------------------
--  trades : the trading journal
-- ---------------------------------------------------------------------
create table if not exists public.trades (
  id           uuid primary key default gen_random_uuid(),
  created_at   timestamptz not null default now(),
  ticker       text not null,
  direction    text not null default 'long',     -- 'long' | 'short'
  setup        text,                              -- strategy / setup name
  entry_date   date,
  entry_price  numeric,
  exit_date    date,
  exit_price   numeric,
  quantity     numeric,
  stop_price   numeric,
  target_price numeric,
  fees         numeric default 0,
  status       text not null default 'open',      -- 'open' | 'closed'
  notes        text
);

-- ---------------------------------------------------------------------
--  scan_cache : single-row JSON cache written by the scheduled scan
-- ---------------------------------------------------------------------
create table if not exists public.scan_cache (
  id         int primary key,                     -- always 1
  payload    jsonb,
  updated_at timestamptz not null default now()
);

-- =====================================================================
--  Row-Level Security
--  NOTE: This app is configured for "no login / single shared space".
--  The anon (public) key can read & write trades. Anyone with your site
--  URL can view and edit the journal. Keep the URL private, or switch on
--  Supabase Auth later if you want per-user isolation.
-- =====================================================================
alter table public.trades     enable row level security;
alter table public.scan_cache enable row level security;

-- trades: anon can do everything (shared journal)
drop policy if exists "trades anon all" on public.trades;
create policy "trades anon all" on public.trades
  for all to anon using (true) with check (true);

-- scan_cache: anon can READ; only the server (service_role) writes it
drop policy if exists "scan_cache anon read" on public.scan_cache;
create policy "scan_cache anon read" on public.scan_cache
  for select to anon using (true);

-- Allow anon to write the cache too, so the on-demand "Refresh" button
-- works even before the scheduled function runs. Remove this if you only
-- want the server to write the cache.
drop policy if exists "scan_cache anon write" on public.scan_cache;
create policy "scan_cache anon write" on public.scan_cache
  for all to anon using (true) with check (true);

-- seed the cache row so the first read never 404s
insert into public.scan_cache (id, payload) values (1, null)
  on conflict (id) do nothing;
