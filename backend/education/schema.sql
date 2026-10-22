CREATE TABLE IF NOT EXISTS education_material (
    material_id TEXT PRIMARY KEY,
    recommendation_id TEXT NOT NULL,
    quarter TEXT NOT NULL REFERENCES quarterly_summary(quarter) ON DELETE CASCADE,
    zone_name TEXT NOT NULL,
    ppe_type TEXT NOT NULL,
    education_topic TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    checklist_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','superseded')),
    provider TEXT NOT NULL DEFAULT 'manual',
    model TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_by TEXT,
    published_at TEXT,
    version INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_material_scope ON education_material(quarter,zone_name,ppe_type,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_material_published ON education_material(quarter,zone_name,ppe_type) WHERE status='published';
