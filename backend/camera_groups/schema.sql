CREATE TABLE IF NOT EXISTS camera_group (
    group_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS camera_group_member (
    group_id TEXT NOT NULL REFERENCES camera_group(group_id) ON DELETE CASCADE,
    camera_id TEXT NOT NULL REFERENCES camera_info(camera_id) ON DELETE CASCADE,
    PRIMARY KEY(group_id,camera_id)
);
CREATE INDEX IF NOT EXISTS idx_camera_group_member ON camera_group_member(camera_id);
