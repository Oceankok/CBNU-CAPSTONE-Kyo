CREATE TABLE IF NOT EXISTS auto_decision_shadow_run (
    run_id TEXT PRIMARY KEY,
    actor_id TEXT REFERENCES app_user(user_id) ON DELETE SET NULL,
    request_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    item_count INTEGER NOT NULL CHECK(item_count BETWEEN 1 AND 20),
    UNIQUE(actor_id,request_key)
);
CREATE TABLE IF NOT EXISTS auto_decision_shadow_item (
    run_id TEXT NOT NULL REFERENCES auto_decision_shadow_run(run_id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES candidate_event(event_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0 AND ordinal < 20),
    result_json TEXT NOT NULL,
    PRIMARY KEY(run_id,event_id),
    UNIQUE(run_id,ordinal)
);
CREATE INDEX IF NOT EXISTS idx_auto_shadow_event ON auto_decision_shadow_item(event_id,run_id);
