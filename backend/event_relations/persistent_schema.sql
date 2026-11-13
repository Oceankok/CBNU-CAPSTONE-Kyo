CREATE TABLE IF NOT EXISTS saved_event_relation (
    relation_id TEXT PRIMARY KEY,
    event_a TEXT NOT NULL REFERENCES candidate_event(event_id) ON DELETE CASCADE,
    event_b TEXT NOT NULL REFERENCES candidate_event(event_id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL UNIQUE,
    evidence_json TEXT NOT NULL,
    saved_by TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    released_by TEXT,
    released_at TEXT,
    CHECK(event_a < event_b),
    CHECK((released_by IS NULL AND released_at IS NULL) OR (released_by IS NOT NULL AND released_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_saved_relation_a ON saved_event_relation(event_a,saved_at,relation_id);
CREATE INDEX IF NOT EXISTS idx_saved_relation_b ON saved_event_relation(event_b,saved_at,relation_id);
