/**
 * Triggers the GitHub Actions scan workflow on demand (workflow_dispatch).
 *
 * The website's "Refresh" button calls this; GitHub then runs the Python
 * scanner (which CAN reach Yahoo) and writes fresh data to Supabase, which the
 * page picks up by polling. This removes all reliance on the flaky cron.
 *
 * Env (set in Netlify, never in the repo/browser):
 *   GH_DISPATCH_TOKEN  fine-grained PAT with Actions: read & write on the repo
 *   GH_REPO            optional, defaults to bryonjelliott/trading-toolkit
 */
const WORKFLOW_FILE = "scan.yml";

export const handler = async () => {
  const token = process.env.GH_DISPATCH_TOKEN;
  const repo = process.env.GH_REPO || "bryonjelliott/trading-toolkit";
  if (!token) {
    return json(500, { ok: false, error: "GH_DISPATCH_TOKEN not set in Netlify env." });
  }
  try {
    const res = await fetch(
      `https://api.github.com/repos/${repo}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "trading-toolkit",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: "main" }),
      }
    );
    if (res.status === 204) return json(200, { ok: true });
    const text = await res.text();
    return json(res.status, { ok: false, status: res.status, error: text.slice(0, 300) });
  } catch (e) {
    return json(500, { ok: false, error: String(e?.message || e) });
  }
};

function json(statusCode, body) {
  return {
    statusCode,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
    body: JSON.stringify(body),
  };
}
