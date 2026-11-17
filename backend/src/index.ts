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

async function me(env: Env, t: Token) {
  return json({ name: t.name, used: await used(env, t), limit: t.monthly_limit, max_seconds: Number(env.MAX_SECONDS) });
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
  const outputs: Record<string, string> = {};
  for (const s of STEMS) outputs[s] = await signed(env, "PUT", `/w/${id}/${s}.flac`);
  const r = await runpod(env, "run", {
    input: { input_url: await signed(env, "GET", `/w/${id}/input.flac`), outputs, stems: STEMS, overlap: Number(j.song_hash.split("-o").pop()) },
  });
  await env.DB.prepare("UPDATE jobs SET status = 'queued', runpod_id = ?, started_at = ? WHERE id = ?")
    .bind(r.id, now(), id).run();
  note(`${t.name}: sent to RunPod for separation`, { user: t.name, job: id, runpod: r.id });
  return json({ id, status: "queued" }, 202);
}

async function status(env: Env, t: Token, id: string) {
  const j = await job(env, t, id);
  if (!j) return fail(404, "no such job");
  if (j.status === "done") return json({ id, status: "done", cached: !!j.cached, stems: stemLinks(id) });
  if (j.status !== "queued" && j.status !== "running") return json({ id, status: j.status, error: j.error });

  const r = await runpod(env, `status/${j.runpod_id}`);
  const next = ({ IN_QUEUE: "queued", IN_PROGRESS: "running", COMPLETED: "done" } as Record<string, string>)[r.status] ?? "failed";
  const error = next === "failed" ? String(r.error ?? r.status) : null;
  const ended = next === "done" || next === "failed";
  await env.DB.prepare("UPDATE jobs SET status = ?, finished_at = ?, gpu_seconds = ?, error = ? WHERE id = ?")
    .bind(next, ended ? now() : null, r.executionTime ? r.executionTime / 1000 : null, error, id).run();
  if (ended) {
    await purgeInput(env, id);
    note(`${t.name}: separation ${next === "done" ? "finished" : "failed"}`, { user: t.name, job: id, status: next,
      gpu_s: r.executionTime / 1000 || null, wait_s: now() - j.started_at, worker: r.workerId ?? null, error });
  }
  if (next === "done") {
    await env.DB.prepare(`INSERT INTO songs (token_hash, song_hash, created_at, accessed_at) VALUES (?, ?, ?, ?)
                          ON CONFLICT DO UPDATE SET accessed_at = excluded.accessed_at`)
      .bind(t.hash, j.song_hash, now(), now()).run();
    return json({ id, status: "done", cached: false, stems: stemLinks(id) });
  }
  if (next === "failed") return json({ id, status: next, error });
  return json({ id, status: next, progress: r.output?.progress ?? null });
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

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const p = url.pathname.split("/").filter(Boolean);
    try {
      if (p[0] === "w" && p.length === 3 && (req.method === "GET" || req.method === "PUT")) {
        return await workerFile(env, req, url, p[1], p[2]);
      }
      if (p[0] !== "v1") return fail(404, "not found");
      const t = await auth(env, req);
      if (!t) {
        note("refused: invalid or revoked key", { path: url.pathname });
        return fail(401, "invalid or revoked key");
      }
      if (p[1] === "me" && req.method === "GET") return await me(env, t);
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
  async scheduled(_c: ScheduledController, env: Env): Promise<void> {
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
