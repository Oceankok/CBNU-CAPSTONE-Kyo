CREATE TABLE IF NOT EXISTS education_translation_job (
 job_id TEXT PRIMARY KEY,
 source_kind TEXT NOT NULL CHECK(source_kind IN ('material','bundle')),
 source_id TEXT NOT NULL, source_version INTEGER NOT NULL, language TEXT NOT NULL,
 content_digest TEXT NOT NULL, validation_reference TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('queued','dispatched','ready','failed','unknown','stale')),
 created_at TEXT NOT NULL, dispatched_at TEXT, deadline_at TEXT, claim_token TEXT,
 completed_at TEXT, failure_code TEXT, model TEXT,
 title TEXT, body TEXT, checklist_json TEXT,
 UNIQUE(source_kind,source_id,source_version,language)
);
CREATE INDEX IF NOT EXISTS idx_education_translation_job_queue ON education_translation_job(state,created_at);
CREATE TABLE IF NOT EXISTS education_translation_budget (
 bucket TEXT PRIMARY KEY, dispatched_count INTEGER NOT NULL DEFAULT 0,
 source_chars INTEGER NOT NULL DEFAULT 0
);
