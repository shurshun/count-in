import { admin } from "./admin";

// Count-In API: hand-issued tokens in front of a RunPod separation endpoint.
//
// The app talks to /v1 with its token. The RunPod worker talks to /w with short-lived signed URLs, so it holds
// no secrets. Uploaded songs live at most a day in R2; separated parts stay 30 days after their last use, per
// token and song hash, so a song separated before comes back without running the model or using the quota.

export interface Env {
  DB: D1Database;
  AUDIO: R2Bucket;
  RUNPOD_BASE: string;
  RUNPOD_API_KEY: string;
  RUNPOD_ENDPOINT: string;
  SIGNING_KEY: string;
  PUBLIC_URL: string;
  MAX_SECONDS: string;
  OVERLAP: string;
  TELEGRAM_TOKEN: string;
  TELEGRAM_CHAT: string;
  RUNPOD_QUEUE: Queue<Step>;
  ADMIN_KEY: string;
  MUSIC_ORIGIN: string;
  MUSIC_SECRET: string;
}

const STEMS = ["guitar", "vocals", "bass", "drums", "piano"];
const MAX_INPUT_BYTES = 120 * 1024 * 1024;
const URL_TTL_S = 2 * 3600; // a cold start plus a long song fits
const KEEP_INPUT_S = 24 * 3600;
const KEEP_STEMS_S = 30 * 24 * 3600;
const HASH = /^[0-9a-f]{16,64}$/;
const OVERLAPS = [2, 4, 8]; // windows per chunk; more is slower and a little cleaner

type Token = { hash: string; name: string; monthly_limit: number };

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const fail = (status: number, error: string, extra: object = {}) => json({ error, ...extra }, status);
const now = () => Math.floor(Date.now() / 1000);

// One line per step of a job in Workers Logs: msg reads as a sentence, the fields can be filtered on.
const note = (msg: string, fields: Record<string, unknown>) => console.log({ msg, ...fields });
const mb = (bytes: number) => Math.round(bytes / 1e5) / 10;

async function sha256(text: string): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function hmac(key: string, text: string): Promise<string> {
  const k = await crypto.subtle.importKey("raw", new TextEncoder().encode(key), { name: "HMAC", hash: "SHA-256" },
    false, ["sign"]);
  const s = await crypto.subtle.sign("HMAC", k, new TextEncoder().encode(text));
  return [...new Uint8Array(s)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function signed(env: Env, method: string, path: string): Promise<string> {
  const exp = now() + URL_TTL_S;
  const sig = await hmac(env.SIGNING_KEY, `${method}\n${path}\n${exp}`);
  return `${env.PUBLIC_URL}${path}?exp=${exp}&sig=${sig}`;
}

async function verified(env: Env, req: Request, url: URL): Promise<boolean> {
  const exp = Number(url.searchParams.get("exp"));
  if (!exp || exp < now()) return false;
  const want = await hmac(env.SIGNING_KEY, `${req.method}\n${url.pathname}\n${exp}`);
  return want === url.searchParams.get("sig");
}

function monthStart(): number {
  const d = new Date();
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) / 1000;
}

async function used(env: Env, t: Token): Promise<number> {
  const r = await env.DB.prepare("SELECT COUNT(*) AS n FROM jobs WHERE token_hash = ? AND started_at >= ? AND status != 'failed'")
    .bind(t.hash, monthStart()).first<{ n: number }>();
  return r?.n ?? 0;
}

async function auth(env: Env, req: Request): Promise<Token | null> {
  const m = (req.headers.get("authorization") ?? "").match(/^Bearer (\S+)$/);
  if (!m) return null;
  return await env.DB.prepare("SELECT hash, name, monthly_limit FROM tokens WHERE hash = ? AND revoked_at IS NULL")
    .bind(await sha256(m[1])).first<Token>();
}

async function runpod(env: Env, path: string, body?: unknown): Promise<any> {
  const r = await fetch(`${env.RUNPOD_BASE}/${env.RUNPOD_ENDPOINT}/${path}`, {
    method: body ? "POST" : "GET",
    headers: { authorization: `Bearer ${env.RUNPOD_API_KEY}`, "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`runpod ${path}: ${r.status} ${await r.text()}`);
  return r.json();
}

async function job(env: Env, t: Token, id: string): Promise<any> {
  return env.DB.prepare("SELECT * FROM jobs WHERE id = ? AND token_hash = ?").bind(id, t.hash).first();
}

const stemKey = (tokenHash: string, songHash: string, name: string) => `stems/${tokenHash}/${songHash}/${name}`;
const stemLinks = (id: string) => Object.fromEntries(STEMS.map((s) => [s, `/v1/jobs/${id}/stems/${s}.flac`]));

// --- app routes ---

function musicEnabled(env: Env): boolean {
  return Boolean(env.MUSIC_ORIGIN && env.MUSIC_SECRET);
}

async function music(env: Env, t: Token, req: Request, url: URL): Promise<Response> {
  if (!musicEnabled(env)) return fail(403, "Music search is disabled");
  const path = url.pathname.slice("/v1/music".length);
  const get = /^\/(artists|artists\/\d+\/tracks|tracks\/\d+\/quality|jobs\/[a-f0-9]{32}(\/audio)?)$/;
  const allowed = (req.method === "GET" && get.test(path))
    || (req.method === "POST" && path === "/jobs")
    || (req.method === "DELETE" && /^\/jobs\/[a-f0-9]{32}$/.test(path));
  if (!allowed) return fail(404, "not found");
  const origin = new URL(env.MUSIC_ORIGIN);
  origin.pathname = "/import" + path;
  const query = url.searchParams.get("q");
  if (query !== null) origin.searchParams.set("q", query.slice(0, 100));
  let body: string | undefined;
  if (req.method === "POST") {
    // Fixed, small JSON body. The client cannot supply an origin path or URL.
    const reader = req.body?.getReader();
    if (!reader) return fail(400, "track_id required");
    const decoder = new TextDecoder();
    let input = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      if (input.length + value.length > 128) {
        await reader.cancel();
        return fail(413, "request too large");
      }
      input += decoder.decode(value, { stream: true });
    }
    let track: number;
    try { track = JSON.parse(input + decoder.decode()).track_id; } catch { return fail(400, "invalid JSON"); }
    if (!Number.isSafeInteger(track) || track < 1) return fail(422, "invalid track_id");
    body = JSON.stringify({ track_id: track });
  }
  const r = await fetch(origin, {
    method: req.method, body, redirect: "manual",
    headers: {
      "content-type": "application/json",
      "x-music-secret": env.MUSIC_SECRET,
      "x-music-owner": await hmac(env.MUSIC_SECRET, t.hash),
      "x-musicbox-client": "1",
    },
  });
  if (r.status >= 300 && r.status < 400) return fail(502, "Unexpected redirect from music origin");
  const headers = new Headers({ "cache-control": "private, no-store" });
  for (const field of ["content-type", "content-length", "content-disposition"]) {
    const value = r.headers.get(field);
    if (value) headers.set(field, value);
  }
  return new Response(r.body, { status: r.status, headers });
}

async function me(env: Env, t: Token) {
  return json({ name: t.name, used: await used(env, t), limit: t.monthly_limit, max_seconds: Number(env.MAX_SECONDS),
    music_search: musicEnabled(env) });
}

async function create(env: Env, t: Token, req: Request) {
  const body = await req.json<{ duration_s?: number; hash?: string; overlap?: number }>().catch(() => ({} as Record<string, never>));
  const dur = Number(body.duration_s);
  if (!(dur > 0) || !HASH.test(String(body.hash ?? ""))) return fail(400, "duration_s and a hex hash are required");
  const overlap = body.overlap ?? Number(env.OVERLAP);
  if (!OVERLAPS.includes(overlap)) return fail(400, `overlap must be one of ${OVERLAPS.join(", ")}`);
  const hash = `${body.hash}-o${overlap}`; // parts made with another overlap are another song for the cache
  const id = crypto.randomUUID();
  const kept = await env.DB.prepare("SELECT 1 FROM songs WHERE token_hash = ? AND song_hash = ?").bind(t.hash, hash).first();
  if (kept) {
    await env.DB.batch([
      env.DB.prepare("UPDATE songs SET accessed_at = ? WHERE token_hash = ? AND song_hash = ?").bind(now(), t.hash, hash),
      env.DB.prepare(`INSERT INTO jobs (id, token_hash, status, duration_s, created_at, finished_at, song_hash, cached)
                      VALUES (?, ?, 'done', ?, ?, ?, ?, 1)`).bind(id, t.hash, dur, now(), now(), hash),
    ]);
    note(`${t.name}: song found in the cache, no upload`, { user: t.name, job: id, song_s: Math.round(dur), overlap });
    return json({ id, status: "done", cached: true, stems: stemLinks(id) }, 201);
  }
  if (dur > Number(env.MAX_SECONDS)) note(`${t.name}: song too long`, { user: t.name, song_s: Math.round(dur) });
  if (dur > Number(env.MAX_SECONDS)) return fail(413, "song too long", { max_seconds: Number(env.MAX_SECONDS) });
  if ((await used(env, t)) >= t.monthly_limit) {
    note(`${t.name}: monthly limit reached`, { user: t.name, limit: t.monthly_limit });
    return fail(429, "monthly limit reached", { limit: t.monthly_limit });
  }
  await env.DB.prepare(`INSERT INTO jobs (id, token_hash, status, duration_s, created_at, song_hash)
                        VALUES (?, ?, 'uploading', ?, ?, ?)`).bind(id, t.hash, dur, now(), hash).run();
  note(`${t.name}: new song, waiting for the upload`, { user: t.name, job: id, song_s: Math.round(dur), overlap });
  return json({ id, status: "uploading", upload: `/v1/jobs/${id}/input`, max_bytes: MAX_INPUT_BYTES }, 201);
}

async function upload(env: Env, t: Token, id: string, req: Request) {
  const j = await job(env, t, id);
  if (!j) return fail(404, "no such job");
  if (j.status !== "uploading") return fail(409, "already uploaded");
  const size = Number(req.headers.get("content-length"));
  if (!size || size > MAX_INPUT_BYTES) return fail(413, "input must be 1 byte to 120 MB with a Content-Length");
  await env.AUDIO.put(`jobs/${id}/input.flac`, req.body);
  await env.DB.prepare("UPDATE jobs SET input_bytes = ? WHERE id = ?").bind(size, id).run();
  note(`${t.name}: song uploaded, ${mb(size)} MB`, { user: t.name, job: id, mb: mb(size) });
  return json({ ok: true });
}

async function start(env: Env, t: Token, id: string) {
  const j = await job(env, t, id);
  if (!j) return fail(404, "no such job");
  if (j.status !== "uploading" || !j.input_bytes) return fail(409, "upload the input first");
  if ((await used(env, t)) >= t.monthly_limit) return fail(429, "monthly limit reached", { limit: t.monthly_limit });
  await env.DB.prepare("UPDATE jobs SET status = 'queued', started_at = ? WHERE id = ?").bind(now(), id).run();
  await env.RUNPOD_QUEUE.send({ id, op: "run" });
  return json({ id, status: "queued" }, 202);
}

// RunPod refuses Worker subrequests made on behalf of clients from some countries, so every RunPod call
// runs in the queue consumer, which has no client. Requests from the app only read and write D1.
type Step = { id: string; op: "run" | "poll"; fails?: number };
const POLL_S = 2;
const MAX_FAILS = 5;

async function step(env: Env, m: Step) {
  const j = await env.DB.prepare("SELECT jobs.*, tokens.name FROM jobs JOIN tokens ON tokens.hash = jobs.token_hash WHERE id = ?")
    .bind(m.id).first<any>();
  if (!j || (j.status !== "queued" && j.status !== "running")) return;
  if (m.op === "run") {
    if (j.runpod_id) return; // a repeated delivery
    const outputs: Record<string, string> = {};
    for (const s of STEMS) outputs[s] = await signed(env, "PUT", `/w/${j.id}/${s}.flac`);
    const r = await runpod(env, "run", {
      input: { input_url: await signed(env, "GET", `/w/${j.id}/input.flac`), outputs, stems: STEMS, overlap: Number(j.song_hash.split("-o").pop()) },
    });
    await env.DB.prepare("UPDATE jobs SET runpod_id = ? WHERE id = ?").bind(r.id, j.id).run();
    note(`${j.name}: sent to RunPod for separation`, { user: j.name, job: j.id, runpod: r.id });
    await env.RUNPOD_QUEUE.send({ id: j.id, op: "poll" }, { delaySeconds: POLL_S });
    return;
  }
  const r = await runpod(env, `status/${j.runpod_id}`);
  const next = ({ IN_QUEUE: "queued", IN_PROGRESS: "running", COMPLETED: "done" } as Record<string, string>)[r.status] ?? "failed";
  const error = next === "failed" ? String(r.error ?? r.status) : null;
  const ended = next === "done" || next === "failed";
  const progress = r.output?.progress ? JSON.stringify(r.output.progress) : null;
  await env.DB.prepare("UPDATE jobs SET status = ?, progress = ?, finished_at = ?, gpu_seconds = ?, error = ? WHERE id = ?")
    .bind(next, progress, ended ? now() : null, r.executionTime ? r.executionTime / 1000 : null, error, j.id).run();
  if (!ended) {
    await env.RUNPOD_QUEUE.send({ id: j.id, op: "poll" }, { delaySeconds: POLL_S });
    return;
  }
  await purgeInput(env, j.id);
  note(`${j.name}: separation ${next === "done" ? "finished" : "failed"}`, { user: j.name, job: j.id, status: next,
    gpu_s: r.executionTime / 1000 || null, wait_s: now() - j.started_at, worker: r.workerId ?? null, error });
  if (next === "done") {
    await env.DB.prepare(`INSERT INTO songs (token_hash, song_hash, created_at, accessed_at) VALUES (?, ?, ?, ?)
                          ON CONFLICT DO UPDATE SET accessed_at = excluded.accessed_at`)
      .bind(j.token_hash, j.song_hash, now(), now()).run();
  }
}

async function status(env: Env, t: Token, id: string) {
  const j = await job(env, t, id);
  if (!j) return fail(404, "no such job");
  if (j.status === "done") return json({ id, status: "done", cached: !!j.cached, stems: stemLinks(id) });
  if (j.status !== "queued" && j.status !== "running") return json({ id, status: j.status, error: j.error });
  return json({ id, status: j.status, progress: j.progress ? JSON.parse(j.progress) : null });
}

async function stem(env: Env, t: Token, id: string, name: string) {
  const j = await job(env, t, id);
  if (!j || j.status !== "done") return fail(404, "no such job");
  const o = await env.AUDIO.get(stemKey(t.hash, j.song_hash, name));
  if (!o) return fail(404, "no such file");
  note(`${t.name}: downloaded ${name}`, { user: t.name, job: id, part: name, mb: mb(o.size) });
  return new Response(o.body, { headers: { "content-type": "audio/flac", "content-length": String(o.size) } });
}

async function remove(env: Env, t: Token, id: string) {
  if (!(await job(env, t, id))) return fail(404, "no such job");
  await purgeInput(env, id);
  return json({ ok: true });
}

async function purgeInput(env: Env, id: string) {
  await env.AUDIO.delete(`jobs/${id}/input.flac`);
}

// --- worker routes (signed URLs) ---

async function workerFile(env: Env, req: Request, url: URL, id: string, name: string) {
  if (!(await verified(env, req, url))) return fail(403, "bad or expired signature");
  if (req.method === "GET") {
    const o = await env.AUDIO.get(`jobs/${id}/${name}`);
    if (o) note("RunPod fetched the song", { job: id, mb: mb(o.size) });
    return o ? new Response(o.body, { headers: { "content-length": String(o.size) } }) : fail(404, "no such file");
  }
  const j = await env.DB.prepare("SELECT token_hash, song_hash FROM jobs WHERE id = ?").bind(id)
    .first<{ token_hash: string; song_hash: string }>();
  if (!j || !STEMS.includes(name.replace(/\.flac$/, ""))) return fail(404, "no such job");
  const size = Number(req.headers.get("content-length"));
  if (!size || size > MAX_INPUT_BYTES) return fail(413, "file too large");
  await env.AUDIO.put(stemKey(j.token_hash, j.song_hash, name), req.body);
  note(`RunPod stored ${name}`, { job: id, part: name, mb: mb(size) });
  return json({ ok: true });
}

// --- bug reports (no key: anyone with the app can send one) ---

const REPORT_TEXT = 4000;
const REPORT_LOG = 200 * 1024;
const REPORTS_PER_IP_HOUR = 3;
const REPORTS_TO_TELEGRAM_DAY = 50;

async function report(env: Env, req: Request) {
  if (Number(req.headers.get("content-length")) > REPORT_TEXT * 4 + REPORT_LOG * 2) return fail(413, "report too large");
  const body = await req.json<{ text?: string; contact?: string; system?: Record<string, unknown>; log?: string }>()
    .catch(() => null);
  const text = String(body?.text ?? "").trim();
  if (!text) return fail(400, "text is required");
  if (text.length > REPORT_TEXT) return fail(413, `text is limited to ${REPORT_TEXT} characters`);
  const log = String(body?.log ?? "");
  if (log.length > REPORT_LOG) return fail(413, "log is limited to 200 KB");
  const contact = String(body?.contact ?? "").slice(0, 200).trim();
  const system = body?.system ? JSON.stringify(body.system).slice(0, 4000) : null;

  const ip = await sha256(`${req.headers.get("cf-connecting-ip") ?? ""}/${env.SIGNING_KEY}`);
  const textHash = await sha256(text);
  const recent = await env.DB.prepare(`SELECT COUNT(*) AS n, SUM(text_hash = ? AND created_at >= ?) AS same
                                       FROM reports WHERE ip_hash = ? AND created_at >= ?`)
    .bind(textHash, now() - 86400, ip, now() - 86400).first<{ n: number; same: number }>();
  const lastHour = await env.DB.prepare("SELECT COUNT(*) AS n FROM reports WHERE ip_hash = ? AND created_at >= ?")
    .bind(ip, now() - 3600).first<{ n: number }>();
  if (recent?.same) return json({ ok: true, duplicate: true });
  if ((lastHour?.n ?? 0) >= REPORTS_PER_IP_HOUR) {
    note("report refused: too many from one address", { ip: ip.slice(0, 8) });
    return fail(429, "too many reports, try again in an hour");
  }

  const r = await env.DB.prepare(`INSERT INTO reports (created_at, ip_hash, text_hash, text, contact, system, log_bytes)
                                  VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id`)
    .bind(now(), ip, textHash, text, contact || null, system, log.length).first<{ id: number }>();
  const id = r!.id;
  const today = await env.DB.prepare("SELECT COUNT(*) AS n FROM reports WHERE sent = 1 AND created_at >= ?")
    .bind(now() - 86400).first<{ n: number }>();
  if ((today?.n ?? 0) >= REPORTS_TO_TELEGRAM_DAY) {
    note("report kept, Telegram quota for the day used up", { report: id });
    return json({ ok: true, id });
  }
  const sent = await telegram(env, id, text, contact, body?.system, log);
  if (sent) await env.DB.prepare("UPDATE reports SET sent = 1 WHERE id = ?").bind(id).run();
  note(`report ${id} ${sent ? "sent to Telegram" : "kept, Telegram failed"}`, { report: id, log_kb: Math.round(log.length / 1024) });
  return json({ ok: true, id });
}

async function telegram(env: Env, id: number, text: string, contact: string, system: unknown, log: string) {
  const sys = system && typeof system === "object"
    ? Object.entries(system as Record<string, unknown>).map(([k, v]) => `${k}: ${v}`).join("\n") : "";
  const parts = [`Отчёт #${id}`, text, contact && `Связь: ${contact}`, sys].filter(Boolean);
  const api = `https://api.telegram.org/bot${env.TELEGRAM_TOKEN}`;
  const msg = await fetch(`${api}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT, text: parts.join("\n\n").slice(0, 4096) }),
  });
  if (!msg.ok) {
    console.error("telegram sendMessage", msg.status, await msg.text());
    return false;
  }
  if (log) {
    const form = new FormData();
    form.append("chat_id", env.TELEGRAM_CHAT);
    form.append("caption", `Журнал к отчёту #${id}`);
    form.append("document", new Blob([log], { type: "text/plain" }), `count-in-${id}.log`);
    const doc = await fetch(`${api}/sendDocument`, { method: "POST", body: form });
    if (!doc.ok) console.error("telegram sendDocument", doc.status, await doc.text());
  }
  return true;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const p = url.pathname.split("/").filter(Boolean);
    try {
      if (p[0] === "w" && p.length === 3 && (req.method === "GET" || req.method === "PUT")) {
        return await workerFile(env, req, url, p[1], p[2]);
      }
      if (p[0] === "admin") return await admin(env, req, url);
      if (p[0] !== "v1") return fail(404, "not found");
      if (p[1] === "report" && p.length === 2 && req.method === "POST") return await report(env, req);
      const t = await auth(env, req);
      if (!t) {
        note("refused: invalid or revoked key", { path: url.pathname });
        return fail(401, "invalid or revoked key");
      }
      if (p[1] === "me" && req.method === "GET") return await me(env, t);
      if (p[1] === "music") return await music(env, t, req, url);
      if (p[1] !== "jobs") return fail(404, "not found");
      if (p.length === 2 && req.method === "POST") return await create(env, t, req);
      const id = p[2];
      if (p.length === 3 && req.method === "GET") return await status(env, t, id);
      if (p.length === 3 && req.method === "DELETE") return await remove(env, t, id);
      if (p[3] === "input" && req.method === "PUT") return await upload(env, t, id, req);
      if (p[3] === "start" && req.method === "POST") return await start(env, t, id);
      if (p[3] === "stems" && p[4] && req.method === "GET") return await stem(env, t, id, p[4]);
      return fail(404, "not found");
    } catch (e) {
      console.error(e);
      return fail(502, "upstream error");
    }
  },

  // Uploaded songs go after a day, separated parts 30 days after their last use.
  async queue(batch: MessageBatch<Step>, env: Env): Promise<void> {
    for (const m of batch.messages) {
      try {
        await step(env, m.body);
      } catch (e) {
        const fails = (m.body.fails ?? 0) + 1;
        console.error(e);
        if (fails < MAX_FAILS) {
          await env.RUNPOD_QUEUE.send({ ...m.body, fails }, { delaySeconds: POLL_S * fails });
        } else {
          await env.DB.prepare("UPDATE jobs SET status = 'failed', finished_at = ?, error = ? WHERE id = ? AND status IN ('queued', 'running')")
            .bind(now(), `RunPod unreachable: ${String(e).slice(0, 300)}`, m.body.id).run();
        }
      }
      m.ack();
    }
  },
  async scheduled(_c: ScheduledController, env: Env): Promise<void> {
    // A job whose queue message got lost gets it again within the hour.
    const stuck = await env.DB.prepare("SELECT id, runpod_id FROM jobs WHERE status IN ('queued', 'running') AND started_at < ?")
      .bind(now() - 600).all<{ id: string; runpod_id: string | null }>();
    for (const j of stuck.results) await env.RUNPOD_QUEUE.send({ id: j.id, op: j.runpod_id ? "poll" : "run" });
    for (const o of (await env.AUDIO.list({ prefix: "jobs/" })).objects) {
      if (o.uploaded.getTime() / 1000 < now() - KEEP_INPUT_S) await env.AUDIO.delete(o.key);
    }
    const old = await env.DB.prepare("SELECT token_hash, song_hash FROM songs WHERE accessed_at < ?")
      .bind(now() - KEEP_STEMS_S).all<{ token_hash: string; song_hash: string }>();
    for (const s of old.results) {
      const keys = (await env.AUDIO.list({ prefix: `stems/${s.token_hash}/${s.song_hash}/` })).objects.map((o) => o.key);
      if (keys.length) await env.AUDIO.delete(keys);
      await env.DB.prepare("DELETE FROM songs WHERE token_hash = ? AND song_hash = ?").bind(s.token_hash, s.song_hash).run();
    }
  },
};
