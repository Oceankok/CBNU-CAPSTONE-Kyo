CREATE TABLE IF NOT EXISTS event_group_context (
    event_id TEXT PRIMARY KEY REFERENCES candidate_event(event_id) ON DELETE CASCADE,
    registered_at TEXT NOT NULL,
    observation_time_basis TEXT NOT NULL CHECK(observation_time_basis IN ('utc','unknown'))
);
CREATE TABLE IF NOT EXISTS event_group_membership_snapshot (
    event_id TEXT NOT NULL REFERENCES event_group_context(event_id) ON DELETE CASCADE,
    group_id TEXT NOT NULL,
    group_version INTEGER NOT NULL,
    PRIMARY KEY(event_id,group_id)
);
CREATE INDEX IF NOT EXISTS idx_event_group_snapshot_lookup
    ON event_group_membership_snapshot(group_id,group_version,event_id);
