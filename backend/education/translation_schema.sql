CREATE TABLE IF NOT EXISTS education_translation (
 translation_id TEXT PRIMARY KEY,
 material_id TEXT REFERENCES education_material(material_id) ON DELETE CASCADE,
 bundle_id TEXT REFERENCES education_bundle(bundle_id) ON DELETE CASCADE,
 language TEXT NOT NULL,
 source_version INTEGER NOT NULL,
 title TEXT NOT NULL, body TEXT NOT NULL, checklist_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','superseded')),
 provider TEXT NOT NULL DEFAULT 'manual', model TEXT,
 created_by TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
 published_by TEXT, published_at TEXT,
 version INTEGER NOT NULL DEFAULT 0,
 CHECK ((material_id IS NOT NULL AND bundle_id IS NULL) OR (material_id IS NULL AND bundle_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_education_translation_material ON education_translation(material_id,language,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_education_translation_published ON education_translation(material_id,language) WHERE status='published';
CREATE INDEX IF NOT EXISTS idx_education_translation_bundle ON education_translation(bundle_id,language,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_education_bundle_translation_published ON education_translation(bundle_id,language) WHERE status='published';
