"""Worker memberships; the legacy scalar is a deterministic projection only."""

from fastapi import HTTPException


def zones_for(conn, user_id):
    return [row[0] for row in conn.execute(
        "SELECT zone_name FROM user_zone WHERE user_id=? ORDER BY zone_name", (user_id,))]


def sync_legacy(conn, user_id):
    zones = zones_for(conn, user_id)
    conn.execute("UPDATE app_user SET zone_name=? WHERE user_id=?", (zones[0] if zones else None, user_id))


def replace_user_zones(conn, user_id, zones):
    zones = [zone.strip() for zone in zones]
    if any(not zone or len(zone) > 100 for zone in zones) or len(zones) != len(set(zones)):
        raise HTTPException(422, "zone_names must contain distinct nonblank zone names")
    row = conn.execute("SELECT role FROM app_user WHERE user_id=?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "User not found")
    if row[0] != "worker" and zones:
        raise HTTPException(422, "Administrators cannot be assigned to a worker zone")
    for zone in zones:
        if not conn.execute("SELECT 1 FROM zone_rule WHERE zone_name=?", (zone,)).fetchone():
            raise HTTPException(422, "zone_name must refer to an existing zone rule")
    conn.execute("DELETE FROM user_zone WHERE user_id=?", (user_id,))
    conn.executemany("INSERT INTO user_zone(user_id,zone_name) VALUES(?,?)", [(user_id, zone) for zone in zones])
    sync_legacy(conn, user_id)
