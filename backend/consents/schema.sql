CREATE TABLE IF NOT EXISTS consent_record (
    record_id TEXT PRIMARY KEY,
    site_code TEXT NOT NULL,
    scope_description TEXT NOT NULL,
    form_version TEXT NOT NULL,
    file_key TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    page_count INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),
    confirmed_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_by TEXT,
    revoked_at TEXT,
    revocation_reason TEXT,
    version INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_consent_site ON consent_record(site_code,created_at);
CREATE TABLE IF NOT EXISTS event_consent (
    event_id TEXT PRIMARY KEY REFERENCES candidate_event(event_id) ON DELETE CASCADE,
    record_id TEXT NOT NULL REFERENCES consent_record(record_id),
    linked_by TEXT NOT NULL,
    linked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_consent_record ON event_consent(record_id);
