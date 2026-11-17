#!/usr/bin/env node
// Issue, list and revoke API tokens.
//
//   node scripts/token.mjs create <name> [--limit 50] [--remote]
//   node scripts/token.mjs list [--remote]
//   node scripts/token.mjs revoke <name> [--remote]
//
// Without --remote it works on the local `wrangler dev` database.

import { createHash, randomBytes } from "node:crypto";
import { execFileSync } from "node:child_process";

const args = process.argv.slice(2);
const where = args.includes("--remote") ? "--remote" : "--local";
const opt = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : fallback;
};
const [cmd, name] = args;

function sql(command) {
  const out = execFileSync("npx", ["wrangler", "d1", "execute", "count-in", where, "--json", "--command", command],
    { encoding: "utf8", stdio: ["ignore", "pipe", "inherit"] });
  return JSON.parse(out)[0].results;
}

const quote = (s) => `'${String(s).replaceAll("'", "''")}'`;

if (cmd === "create" && name) {
  const limit = Number(opt("--limit", 50));
  if (!Number.isInteger(limit) || limit < 1) throw new Error("--limit must be a positive integer");
  const token = "cin_" + randomBytes(32).toString("base64url");
  const hash = createHash("sha256").update(token).digest("hex");
  sql(`INSERT INTO tokens (hash, name, monthly_limit, created_at) VALUES (${quote(hash)}, ${quote(name)}, ${limit}, ${Math.floor(Date.now() / 1000)})`);
  console.log(`Token for ${name} (${limit} songs a month). It is shown only once:\n\n${token}\n`);
} else if (cmd === "list") {
  const month = Math.floor(Date.UTC(new Date().getUTCFullYear(), new Date().getUTCMonth(), 1) / 1000);
  console.table(sql(`SELECT t.name, t.monthly_limit AS "limit",
      (SELECT COUNT(*) FROM jobs j WHERE j.token_hash = t.hash AND j.started_at >= ${month}) AS used,
      ROUND((SELECT COALESCE(SUM(gpu_seconds), 0) FROM jobs j WHERE j.token_hash = t.hash), 1) AS gpu_s,
      datetime(t.created_at, 'unixepoch') AS created,
      CASE WHEN t.revoked_at IS NULL THEN '' ELSE datetime(t.revoked_at, 'unixepoch') END AS revoked
    FROM tokens t ORDER BY t.created_at`));
} else if (cmd === "revoke" && name) {
  sql(`UPDATE tokens SET revoked_at = ${Math.floor(Date.now() / 1000)} WHERE name = ${quote(name)} AND revoked_at IS NULL`);
  console.log(`Revoked the tokens of ${name}.`);
} else {
  console.log("usage: token.mjs create <name> [--limit N] | list | revoke <name>  [--remote]");
  process.exit(1);
}
