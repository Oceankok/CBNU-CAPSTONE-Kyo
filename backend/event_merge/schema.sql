CREATE TABLE IF NOT EXISTS event_merge (
    source_event_id TEXT PRIMARY KEY REFERENCES candidate_event(event_id),
    target_event_id TEXT NOT NULL REFERENCES candidate_event(event_id),
    merged_by TEXT NOT NULL,
    merged_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    media_ids_json TEXT NOT NULL,
    request_ids_json TEXT NOT NULL,
    CHECK(source_event_id != target_event_id)
);
CREATE INDEX IF NOT EXISTS idx_event_merge_target ON event_merge(target_event_id);
