-- Hand-issued API tokens. Only the SHA-256 of a token is stored.
CREATE TABLE IF NOT EXISTS tokens (
  hash TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  monthly_limit INTEGER NOT NULL DEFAULT 50,
  created_at INTEGER NOT NULL,
  revoked_at INTEGER
);

-- One row per separation. A job counts against the quota once it starts on RunPod.
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL REFERENCES tokens(hash),
  status TEXT NOT NULL,          -- uploading, queued, running, done, failed, deleted
  duration_s REAL NOT NULL,
  input_bytes INTEGER,
  runpod_id TEXT,
  created_at INTEGER NOT NULL,
  started_at INTEGER,
  finished_at INTEGER,
  gpu_seconds REAL,
  error TEXT,
  song_hash TEXT NOT NULL,       -- the app's hash of the source file and model settings
  cached INTEGER NOT NULL DEFAULT 0
);

-- Separated songs kept per token, so the same file never goes through the model twice.
-- Parts live at stems/<token hash>/<song hash>/<stem>.flac for 30 days after the last use.
CREATE TABLE IF NOT EXISTS songs (
  token_hash TEXT NOT NULL REFERENCES tokens(hash),
  song_hash TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  accessed_at INTEGER NOT NULL,
  PRIMARY KEY (token_hash, song_hash)
);

CREATE INDEX IF NOT EXISTS jobs_by_token ON jobs (token_hash, started_at);
CREATE INDEX IF NOT EXISTS jobs_by_age ON jobs (created_at);
