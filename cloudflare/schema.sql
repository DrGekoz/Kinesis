-- Kinesis Gesture-Map marketplace: one table, because a Gesture-Map is one document.
--
-- The map itself is stored as the exported JSON text, byte for byte, so what a user downloads is
-- exactly what the author exported - and the server can validate it before accepting it without
-- needing to understand every future field.

CREATE TABLE IF NOT EXISTS gesture_maps (
  id             TEXT PRIMARY KEY,          -- uuid
  slug           TEXT UNIQUE NOT NULL,      -- stable, human-readable, used in URLs
  title          TEXT NOT NULL,
  description    TEXT NOT NULL DEFAULT '',
  author_name    TEXT NOT NULL,             -- who made it (asked on submission)
  github_url     TEXT NOT NULL,             -- required, per the upload form
  map_json       TEXT NOT NULL,             -- the exported .json, verbatim
  binding_count  INTEGER NOT NULL DEFAULT 0,
  downloads      INTEGER NOT NULL DEFAULT 0,
  featured       INTEGER NOT NULL DEFAULT 0, -- curated ones sort first
  created_at     TEXT NOT NULL              -- ISO 8601, UTC
);

CREATE INDEX IF NOT EXISTS idx_maps_created  ON gesture_maps (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_maps_download ON gesture_maps (downloads DESC);
CREATE INDEX IF NOT EXISTS idx_maps_author   ON gesture_maps (author_name);

-- Downloads are counted server-side so the number cannot be inflated by the client, and so a
-- download survives the browser window being closed.
CREATE TABLE IF NOT EXISTS download_log (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  map_id     TEXT NOT NULL,
  at         TEXT NOT NULL
);
