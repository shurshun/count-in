-- Apply once to databases initialized before track titles and input retention.
ALTER TABLE jobs ADD COLUMN track_title TEXT;
ALTER TABLE jobs ADD COLUMN input_uploaded_at INTEGER;
