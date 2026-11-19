// Admin pages at /admin: keys, their use, every job with its status and error, bug reports.
// One secret, ADMIN_KEY, opens them; the browser keeps its hash in an HttpOnly cookie on /admin.

import type { Env } from "./index";

const COOKIE = "countin_admin";
const now = () => Math.floor(Date.now() / 1000);

async function sha256(text: string): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

const esc = (v: unknown) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

const when = (s: number | null) => (s ? new Date(s * 1000).toISOString().slice(0, 16).replace("T", " ") : "");
const ago = (s: number | null) => {
  if (!s) return "никогда";
  const d = now() - s;
  if (d < 3600) return `${Math.max(1, Math.round(d / 60))} мин назад`;
  if (d < 86400) return `${Math.round(d / 3600)} ч назад`;
  return `${Math.round(d / 86400)} дн назад`;
};
const mmss = (s: number | null) => (s == null ? "" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);
const monthStart = () => Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(), 1) / 1000;

function page(title: string, body: string, status = 200): Response {
  return new Response(`<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>${esc(title)} · Count-In admin</title><style>
:root { --bg:#fbfbfd; --card:#fff; --border:#e3e3e8; --text:#1d1d1f; --muted:#6e6e73; --accent:#ff9f0a;
        --ok:#1f9d55; --bad:#d70015; --warn:#b26a00; color-scheme: light dark; }
@media (prefers-color-scheme: dark) { :root { --bg:#161618; --card:#26262a; --border:#38383c; --text:#f5f5f7;
        --muted:#a1a1a6; --ok:#30d158; --bad:#ff6961; --warn:#ffb340; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1200px; margin: 0 auto; padding: 16px; }
nav { display: flex; gap: 16px; align-items: center; margin-bottom: 20px; flex-wrap: wrap; }
nav b { margin-right: auto; font-size: 16px; }
a { color: inherit; } nav a { color: var(--muted); text-decoration: none; } nav a:hover { color: var(--text); }
h1 { font-size: 22px; margin: 0 0 4px; } .sub { color: var(--muted); margin: 0 0 16px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 16px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 16px; }
.stats div { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 12px; }
.stats b { display: block; font-size: 20px; } .stats span { color: var(--muted); font-size: 12px; }
.scroll { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; } th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--border); vertical-align: top; white-space: nowrap; }
th { color: var(--muted); font-weight: 500; font-size: 12px; } td.wrap { white-space: normal; min-width: 260px; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; border: 1px solid currentColor; }
.ok { color: var(--ok); } .bad { color: var(--bad); } .warn { color: var(--warn); } .muted { color: var(--muted); }
form.inline { display: inline-flex; gap: 6px; align-items: center; margin: 0; }
input, select, button { font: inherit; padding: 6px 10px; border-radius: 8px; border: 1px solid var(--border); background: var(--card); color: var(--text); }
button { cursor: pointer; } button.primary { background: var(--accent); border-color: var(--accent); color: #1d1d1f; font-weight: 600; }
button.danger { color: var(--bad); }
details summary { cursor: pointer; } pre { white-space: pre-wrap; word-break: break-word; font-size: 12px; margin: 6px 0 0; max-height: 360px; overflow: auto; }
.token { font: 13px ui-monospace, Menlo, monospace; word-break: break-all; padding: 10px; border: 1px dashed var(--accent); border-radius: 8px; }
</style></head><body><main>
<nav><b>Count-In admin</b><a href="/admin">Ключи</a><a href="/admin/jobs">Разделения</a><a href="/admin/reports">Отчёты</a></nav>
${body}</main></body></html>`, { status, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
}

const redirect = (to: string, cookie?: string) =>
  new Response(null, { status: 303, headers: { location: to, ...(cookie ? { "set-cookie": cookie } : {}) } });

// --- job status as a person reads it ---

function state(j: any): [string, string] {
  if (j.status === "uploading") {
    if (j.input_bytes) return ["загружено, не запущено", "warn"];
    return now() - j.created_at > 900 ? ["загрузка не дошла", "bad"] : ["загружается", "warn"];
  }
  if (j.status === "queued") return ["в очереди", "warn"];
  if (j.status === "running") return ["разделяется", "warn"];
  if (j.status === "done") return [j.cached ? "из кэша" : "готово", "ok"];
  if (j.status === "failed") return ["ошибка", "bad"];
  return [j.status, "muted"];
}

function errorCell(e: string | null): string {
  if (!e) return "";
  let short = e;
  try {
    const p = JSON.parse(e);
    short = `${String(p.error_type ?? "").replace(/^<class '(.+)'>$/, "$1")}: ${p.error_message ?? ""}`;
  } catch {}
  return `<details><summary class="bad">${esc(short.slice(0, 120))}</summary><pre>${esc(e)}</pre></details>`;
}

function jobsTable(rows: any[], withUser: boolean): string {
  if (!rows.length) return `<p class="muted">Разделений нет.</p>`;
  const head = `<tr><th>Когда (UTC)</th>${withUser ? "<th>Ключ</th>" : ""}<th>Статус</th><th class="num">Песня</th>
    <th class="num">Файл</th><th class="num">Ожидание</th><th class="num">GPU</th><th>Окна</th><th>Ошибка</th></tr>`;
  const body = rows.map((j) => {
    const [label, cls] = state(j);
    const wait = j.finished_at && j.started_at ? j.finished_at - j.started_at : null;
    const progress = j.progress && (j.status === "running" || j.status === "queued")
      ? (() => { try { const p = JSON.parse(j.progress); return ` ${p.done}/${p.total}`; } catch { return ""; } })() : "";
    return `<tr><td title="${esc(j.id)}">${when(j.created_at)}</td>
      ${withUser ? `<td><a href="/admin/u/${esc(j.token_hash)}">${esc(j.name)}</a></td>` : ""}
      <td><span class="pill ${cls}">${esc(label)}${esc(progress)}</span></td>
      <td class="num">${mmss(j.duration_s)}</td>
      <td class="num">${j.input_bytes ? (j.input_bytes / 1e6).toFixed(1) + " МБ" : ""}</td>
      <td class="num">${mmss(wait)}</td>
      <td class="num">${j.gpu_seconds ? Math.round(j.gpu_seconds) + " с" : ""}</td>
      <td>${esc(String(j.song_hash ?? "").split("-o").pop())}</td>
      <td class="wrap">${errorCell(j.error)}</td></tr>`;
  }).join("");
  return `<div class="scroll"><table>${head}${body}</table></div>`;
}

// --- pages ---

async function keys(env: Env, issued?: { name: string; token: string }) {
  const rows = (await env.DB.prepare(`SELECT t.hash, t.name, t.monthly_limit, t.created_at, t.revoked_at,
      (SELECT COUNT(*) FROM jobs j WHERE j.token_hash = t.hash AND j.started_at >= ? AND j.status != 'failed') AS used,
      (SELECT COUNT(*) FROM jobs j WHERE j.token_hash = t.hash) AS total,
      (SELECT COUNT(*) FROM jobs j WHERE j.token_hash = t.hash AND (j.status = 'failed'
         OR (j.status = 'uploading' AND j.created_at < ?))) AS bad,
      (SELECT COALESCE(SUM(gpu_seconds), 0) FROM jobs j WHERE j.token_hash = t.hash) AS gpu,
      (SELECT MAX(created_at) FROM jobs j WHERE j.token_hash = t.hash) AS last
    FROM tokens t ORDER BY t.revoked_at IS NOT NULL, last DESC NULLS LAST, t.created_at DESC`)
    .bind(monthStart(), now() - 900).all<any>()).results;
  const list = rows.map((t) => `<tr>
      <td><a href="/admin/u/${esc(t.hash)}"><b>${esc(t.name)}</b></a></td>
      <td>${t.revoked_at ? `<span class="pill muted">отозван</span>` : `<span class="pill ok">активен</span>`}</td>
      <td class="num">${t.used} / ${t.monthly_limit}</td><td class="num">${t.total}</td>
      <td class="num ${t.bad ? "bad" : ""}">${t.bad}</td><td class="num">${Math.round(t.gpu / 60)} мин</td>
      <td title="${when(t.last)}">${ago(t.last)}</td><td class="muted">${when(t.created_at).slice(0, 10)}</td></tr>`).join("");
  const shown = issued ? `<div class="card"><p>Ключ для <b>${esc(issued.name)}</b>. Он показан один раз, скопируйте его сейчас.</p>
      <div class="token">${esc(issued.token)}</div></div><script>history.replaceState(null, "", "/admin")</script>` : "";
  return page("Ключи", `<h1>Ключи</h1><p class="sub">Песни за месяц считаются с 1-го числа по UTC; неудачные не считаются.</p>
    ${shown}
    <div class="card"><form class="inline" method="post" action="/admin/keys">
      <input name="name" placeholder="Имя" required maxlength="60">
      <input name="limit" type="number" min="1" value="50" style="width:90px" title="Песен в месяц">
      <button class="primary">Выдать ключ</button></form></div>
    <div class="card scroll"><table><tr><th>Имя</th><th>Статус</th><th class="num">Месяц</th><th class="num">Всего</th>
      <th class="num">Сбоев</th><th class="num">GPU</th><th>Активность</th><th>Выдан</th></tr>${list}</table></div>`);
}

async function user(env: Env, hash: string, days: number) {
  const t = await env.DB.prepare("SELECT * FROM tokens WHERE hash = ?").bind(hash).first<any>();
  if (!t) return page("Нет ключа", `<p>Такого ключа нет.</p>`, 404);
  const since = now() - days * 86400;
  const jobs = (await env.DB.prepare("SELECT * FROM jobs WHERE token_hash = ? AND created_at >= ? ORDER BY created_at DESC LIMIT 500")
    .bind(hash, since).all<any>()).results;
  const s = await env.DB.prepare(`SELECT
      TOTAL(started_at >= ? AND status != 'failed') AS month, COUNT(*) AS total, TOTAL(status = 'done' AND cached = 0) AS done,
      TOTAL(cached) AS cached, TOTAL(status = 'failed') AS failed,
      TOTAL(status = 'uploading' AND input_bytes IS NULL AND created_at < ?) AS lost,
      COALESCE(SUM(gpu_seconds), 0) AS gpu, COUNT(DISTINCT song_hash) AS songs
    FROM jobs WHERE token_hash = ?`).bind(monthStart(), now() - 900, hash).first<any>();
  const stat = (v: unknown, label: string, cls = "") => `<div><b class="${cls}">${esc(v ?? 0)}</b><span>${label}</span></div>`;
  const toggle = t.revoked_at
    ? `<form class="inline" method="post" action="/admin/u/${esc(hash)}/restore"><button>Вернуть ключ</button></form>`
    : `<form class="inline" method="post" action="/admin/u/${esc(hash)}/revoke" onsubmit="return confirm('Отозвать ключ ${esc(t.name)}?')"><button class="danger">Отозвать</button></form>`;
  const periods = [7, 30, 90, 365].map((d) => d === days ? `<b>${d} дн</b>` : `<a href="?days=${d}">${d} дн</a>`).join(" · ");
  return page(t.name, `<h1>${esc(t.name)} ${t.revoked_at ? `<span class="pill muted">отозван ${when(t.revoked_at).slice(0, 10)}</span>` : ""}</h1>
    <p class="sub">Выдан ${when(t.created_at)} UTC · ключ ${esc(hash.slice(0, 8))}…</p>
    <div class="stats">${stat(`${s.month} / ${t.monthly_limit}`, "песен в этом месяце")}${stat(s.total, "запросов всего")}
      ${stat(s.done, "разделено на GPU", "ok")}${stat(s.cached, "из кэша")}${stat(s.failed, "ошибок", s.failed ? "bad" : "")}
      ${stat(s.lost, "загрузок не дошло", s.lost ? "bad" : "")}${stat(`${Math.round(s.gpu / 60)} мин`, "GPU")}${stat(s.songs, "разных песен")}</div>
    <div class="card" style="display:flex;gap:12px;flex-wrap:wrap;align-items:center">
      <form class="inline" method="post" action="/admin/u/${esc(hash)}/limit"><span>Лимит в месяц</span>
        <input name="limit" type="number" min="1" value="${t.monthly_limit}" style="width:90px"><button>Сохранить</button></form>
      ${toggle}</div>
    <div class="card"><p class="sub">За ${periods}</p>${jobsTable(jobs, false)}</div>`);
}

async function allJobs(env: Env, status: string, days: number) {
  const [filter, args] = status === "lost" ? ["AND j.status = 'uploading' AND j.input_bytes IS NULL AND j.created_at < ?", [now() - 900]]
    : status ? ["AND j.status = ?", [status]] : ["", []];
  const rows = (await env.DB.prepare(`SELECT j.*, t.name FROM jobs j JOIN tokens t ON t.hash = j.token_hash
      WHERE j.created_at >= ? ${filter} ORDER BY j.created_at DESC LIMIT 500`)
    .bind(now() - days * 86400, ...args).all<any>()).results;
  const opts = [["", "все"], ["done", "готово"], ["failed", "ошибка"], ["lost", "загрузка не дошла"], ["uploading", "загружается"],
    ["queued", "в очереди"], ["running", "разделяется"]]
    .map(([v, l]) => `<option value="${v}"${v === status ? " selected" : ""}>${l}</option>`).join("");
  return page("Разделения", `<h1>Разделения</h1><p class="sub">Последние 500 за выбранный срок, новые сверху.</p>
    <div class="card"><form class="inline"><select name="status">${opts}</select>
      <select name="days">${[1, 7, 30, 90].map((d) => `<option${d === days ? " selected" : ""}>${d}</option>`).join("")}</select> дн
      <button>Показать</button></form></div>
    <div class="card">${jobsTable(rows, true)}</div>`);
}

async function reports(env: Env) {
  const rows = (await env.DB.prepare("SELECT * FROM reports ORDER BY created_at DESC LIMIT 100").all<any>()).results;
  const list = rows.map((r) => {
    let sys = "";
    try { sys = Object.entries(JSON.parse(r.system ?? "{}")).map(([k, v]) => `${k}: ${v}`).join(" · "); } catch {}
    return `<div class="card"><p class="sub">#${r.id} · ${when(r.created_at)} UTC${r.contact ? ` · ${esc(r.contact)}` : ""}
      ${r.sent ? "" : ` · <span class="warn">не ушёл в Telegram</span>`}</p>
      <details><summary>${esc(String(r.text).split("\n")[0].slice(0, 160))}</summary><pre>${esc(r.text)}</pre></details>
      <p class="muted" style="margin:8px 0 0">${esc(sys)}${r.log_bytes ? ` · лог ${Math.round(r.log_bytes / 1024)} КБ` : ""}</p></div>`;
  }).join("");
  return page("Отчёты", `<h1>Отчёты о проблемах</h1><p class="sub">Из меню «Справка» → «Сообщить о проблеме», последние 100. Логи уходят в Telegram.</p>
    ${list || `<p class="muted">Отчётов нет.</p>`}`);
}

const LOGIN = (msg = "") => page("Вход", `<div class="card" style="max-width:420px;margin:40px auto">
  <h1>Вход</h1>${msg ? `<p class="bad">${esc(msg)}</p>` : ""}
  <form method="post" action="/admin/login" style="display:flex;gap:8px;margin-top:12px">
    <input name="key" type="password" placeholder="ADMIN_KEY" required style="flex:1" autofocus><button class="primary">Войти</button></form></div>`,
  msg ? 401 : 200);

async function signedIn(env: Env, req: Request): Promise<boolean> {
  if (!env.ADMIN_KEY) return false;
  const c = (req.headers.get("cookie") ?? "").split(/;\s*/).find((x) => x.startsWith(COOKIE + "="))?.slice(COOKIE.length + 1);
  return !!c && c === (await sha256(`admin/${env.ADMIN_KEY}`));
}

export async function admin(env: Env, req: Request, url: URL): Promise<Response> {
  const p = url.pathname.split("/").filter(Boolean).slice(1);
  if (p[0] === "login" && req.method === "POST") {
    const key = String((await req.formData()).get("key") ?? "");
    if (!env.ADMIN_KEY || key !== env.ADMIN_KEY) {
      console.log({ msg: "admin: wrong key", ip: req.headers.get("cf-connecting-ip") });
      return LOGIN("Неверный ключ.");
    }
    const value = await sha256(`admin/${env.ADMIN_KEY}`);
    return redirect("/admin", `${COOKIE}=${value}; Path=/admin; HttpOnly; Secure; SameSite=Strict; Max-Age=2592000`);
  }
  if (!(await signedIn(env, req))) return LOGIN();
  if (p[0] === "logout") return redirect("/admin", `${COOKIE}=; Path=/admin; Max-Age=0`);

  if (req.method === "POST") {
    const form = await req.formData();
    const limit = Number(form.get("limit"));
    if (p[0] === "keys") {
      const name = String(form.get("name") ?? "").trim().slice(0, 60);
      if (!name || !Number.isInteger(limit) || limit < 1) return redirect("/admin");
      const bytes = crypto.getRandomValues(new Uint8Array(32));
      const token = "cin_" + btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
      await env.DB.prepare("INSERT INTO tokens (hash, name, monthly_limit, created_at) VALUES (?, ?, ?, ?)")
        .bind(await sha256(token), name, limit, now()).run();
      console.log({ msg: `admin: key issued for ${name}`, user: name, limit });
      return keys(env, { name, token });
    }
    if (p[0] === "u" && p[1]) {
      const h = p[1];
      if (p[2] === "revoke") await env.DB.prepare("UPDATE tokens SET revoked_at = ? WHERE hash = ? AND revoked_at IS NULL").bind(now(), h).run();
      if (p[2] === "restore") await env.DB.prepare("UPDATE tokens SET revoked_at = NULL WHERE hash = ?").bind(h).run();
      if (p[2] === "limit" && Number.isInteger(limit) && limit > 0)
        await env.DB.prepare("UPDATE tokens SET monthly_limit = ? WHERE hash = ?").bind(limit, h).run();
      console.log({ msg: `admin: ${p[2]}`, key: h.slice(0, 8), limit: p[2] === "limit" ? limit : undefined });
      return redirect(`/admin/u/${h}`);
    }
    return redirect("/admin");
  }

  const days = Math.min(3650, Math.max(1, Number(url.searchParams.get("days")) || 30));
  if (p.length === 0) return keys(env);
  if (p[0] === "u" && p[1]) return user(env, p[1], days);
  if (p[0] === "jobs") return allJobs(env, url.searchParams.get("status") ?? "", Number(url.searchParams.get("days")) || 7);
  if (p[0] === "reports") return reports(env);
  return page("Нет страницы", `<p>Нет такой страницы.</p>`, 404);
}
