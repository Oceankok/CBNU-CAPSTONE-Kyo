import hashlib
import json

from fastapi import HTTPException

from backend.db.event_repository import get_connection
from backend.equipment.routes import find_camera, find_equipment, now, serialize, get_camera_policy
from backend.work_areas.geometry import validate_points
from backend.zones.policy import combine_required_ppe, validate_required_ppe


def config(conn, camera_id):
    row = conn.execute('SELECT * FROM camera_area_config WHERE camera_id=?', (camera_id,)).fetchone()
    return dict(row) if row else {'camera_id': camera_id, 'enabled': 0, 'version': 0, 'updated_at': None}


def touch(conn, camera_id, enabled=None):
    current = config(conn, camera_id)
    conn.execute('''INSERT INTO camera_area_config(camera_id,enabled,version,updated_at) VALUES(?,?,?,?)
        ON CONFLICT(camera_id) DO UPDATE SET enabled=excluded.enabled,version=excluded.version,updated_at=excluded.updated_at''',
                 (camera_id, current['enabled'] if enabled is None else int(enabled), current['version']+1, now()))


def areas(conn, camera_id):
    result = []
    for row in conn.execute('SELECT * FROM camera_work_area WHERE camera_id=? ORDER BY area_id', (camera_id,)):
        item = dict(row)
        try:
            item['points'] = validate_points(json.loads(item.pop('points_json')))
        except (ValueError, TypeError):
            raise HTTPException(409, 'stored_work_area_invalid')
        result.append(item)
    return result


def linked_equipment(conn, camera_id, equipment_id):
    camera, equipment = find_camera(conn, camera_id), find_equipment(conn, equipment_id)
    if equipment['status'] != 'active':
        raise HTTPException(409, 'equipment_inactive')
    if equipment['zone_name'] != camera['zone_name']:
        raise HTTPException(409, 'equipment_zone_mismatch')
    if not conn.execute('SELECT 1 FROM camera_equipment WHERE camera_id=? AND equipment_id=?',
                        (camera_id, equipment_id)).fetchone():
        raise HTTPException(409, 'equipment_not_linked_to_camera')
    return serialize(equipment)


def list_areas(conn, camera_id):
    find_camera(conn, camera_id)
    current = config(conn, camera_id)
    return {**current, 'enabled': bool(current['enabled']), 'scope': 'work_area' if current['enabled'] else 'camera',
            'overlap_allowed': True, 'items': areas(conn, camera_id)}


def policy(camera_id):
    with get_connection() as conn:
        conn.execute('BEGIN')
        camera = find_camera(conn, camera_id)
        current = config(conn, camera_id)
        if not current['enabled']:
            # The legacy endpoint keeps its original camera-wide meaning.
            legacy = get_camera_policy(camera_id)
            return {**legacy, 'policy_version': fingerprint(legacy), 'areas': [], 'area_config_version': current['version']}
        rule = conn.execute('SELECT required_ppe FROM zone_rule WHERE zone_name=?', (camera['zone_name'],)).fetchone()
        issues, baseline = [], None
        if camera['status'] != 'active':
            issues.append('camera_inactive')
        if not rule:
            issues.append('zone_rule_missing')
        else:
            try:
                baseline = validate_required_ppe(json.loads(rule[0]))
            except (ValueError, TypeError, AttributeError):
                issues.append('stored_zone_rule_invalid')
        try:
            records = areas(conn, camera_id)
        except HTTPException:
            records = []
            issues.append('stored_work_area_invalid')
        if not records:
            issues.append('work_areas_empty')
        resolved = []
        for item in records:
            try:
                equipment = linked_equipment(conn, camera_id, item['equipment_id'])
                resolved.append({**item, 'equipment_required_ppe': equipment['required_ppe'],
                                 'required_ppe': combine_required_ppe(baseline or [], [equipment['required_ppe']])})
            except HTTPException as error:
                issues.append(f"{item['area_id']}:{error.detail}")
        result = {'camera_id': camera_id, 'zone_name': camera['zone_name'], 'scope': 'work_area',
                  'configured': not issues, 'configuration_issues': issues,
                  'area_config_version': current['version'], 'zone_required_ppe': baseline,
                  'required_ppe': None, 'areas': resolved, 'overlap_allowed': True,
                  'person_anchor': 'bbox_bottom_center', 'boundary_included': True,
                  'event_supported_ppe': ['helmet', 'vest']}
        result['policy_version'] = fingerprint(result)
        return result


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
