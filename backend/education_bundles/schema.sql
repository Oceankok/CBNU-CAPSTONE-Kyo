CREATE TABLE IF NOT EXISTS education_bundle (
 bundle_id TEXT PRIMARY KEY,
 quarter TEXT NOT NULL,
 ppe_type TEXT NOT NULL,
 scope_key TEXT NOT NULL,
 sources_json TEXT NOT NULL,
 title TEXT NOT NULL,
 body TEXT NOT NULL,
 checklist_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','superseded')),
 provider TEXT NOT NULL DEFAULT 'manual', model TEXT,
 created_by TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
 published_by TEXT, published_at TEXT,
 version INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bundle_quarter ON education_bundle(quarter,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_bundle_published ON education_bundle(scope_key) WHERE status='published';
