CREATE TABLE IF NOT EXISTS auto_decision_settings (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 version INTEGER NOT NULL CHECK(version>=1),
 policy_json TEXT NOT NULL,
 updated_by TEXT NOT NULL REFERENCES app_user(user_id),
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auto_decision_settings_history (
 version INTEGER PRIMARY KEY,
 policy_json TEXT NOT NULL,
 changed_by TEXT NOT NULL REFERENCES app_user(user_id),
 changed_at TEXT NOT NULL,
 reason TEXT NOT NULL
);
