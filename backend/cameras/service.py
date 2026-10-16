class CameraStateError(ValueError):
    def __init__(self, code, status_code):
        super().__init__(code)
        self.status_code = status_code


def require_camera(conn, camera_id, *, active=False):
    row = conn.execute('SELECT * FROM camera_info WHERE camera_id=?', (camera_id,)).fetchone()
    if not row:
        raise CameraStateError('camera_not_found', 404)
    if active and row['status'] != 'active':
        raise CameraStateError('camera_inactive', 409)
    return row


def public_camera(conn, camera_id):
    row = conn.execute('''SELECT c.camera_id,c.camera_location,c.zone_name,c.process_type,
        c.camera_angle_type,c.status,n.source_node_id,n.output_node_id FROM camera_info c
        LEFT JOIN camera_node n ON n.camera_id=c.camera_id WHERE c.camera_id=?''', (camera_id,)).fetchone()
    result = dict(row)
    if result['status'] not in {'active', 'inactive'}:
        result['status'] = 'unknown'
    return result
