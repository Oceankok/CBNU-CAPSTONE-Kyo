"""Explicit demo-only database and no-person fixtures. Never seed the default DB."""
import argparse
from getpass import getpass
import os
from pathlib import Path
from urllib.parse import urlsplit

from backend.auth.service import create_user
from backend.db.connection import connect, is_postgres, begin_write
from backend.db.event_repository import insert_candidate_event, insert_event_review, generate_quarterly_stats, generate_education_recommendations
from backend.db.migrations import apply_migrations
from backend.media.paths import PROCESSED_ROOT, storage_key, owned_path, publish_file
from backend.zones.membership import replace_user_zones

ASSETS = Path(__file__).resolve().parent / 'demo_assets'
ZONES = ['DEMO_프레스 구역', 'DEMO_자재 구역']
SPECS = [('pending','2026-04-03'), ('pending','2026-07-05'), ('pending','2026-10-05'),
         ('confirmed','2026-04-09'), ('confirmed','2026-07-12'), ('false_positive','2026-07-19'),
         ('hold','2026-10-08'), ('unreviewable','2026-10-09'), ('processing','2026-10-10'), ('failed','2026-10-11'),
         ('confirmed','2026-10-12'), ('confirmed','2026-10-12')]


def seed_demo(password, *, reset=False, trusted_fixture=False):
    if len(password) < 8:
        raise ValueError('Demo password must contain at least 8 characters')
    if not trusted_fixture:
        if is_postgres():
            if urlsplit(os.environ['PPE_DATABASE_URL']).path != '/kyo_demo':
                raise ValueError('Demo seed requires a separate PostgreSQL database named kyo_demo')
        else:
            if not os.environ.get('PPE_DB_PATH'):
                target = Path(__file__).resolve().parents[2] / '.private_media/demo/kyo_demo.db'
                target.parent.mkdir(parents=True, exist_ok=True)
                os.environ['PPE_DB_PATH'] = str(target)
            if Path(os.environ['PPE_DB_PATH']).name != 'kyo_demo.db':
                raise ValueError('Demo seed requires a separate kyo_demo.db file')
            Path(os.environ['PPE_DB_PATH']).parent.mkdir(parents=True,exist_ok=True)
    if is_postgres():
        from backend.db.postgres import initialize
        initialize(seed=False)
    else:
        with connect() as conn:
            conn.executescript((Path(__file__).resolve().parent / 'schema.sql').read_text(encoding='utf-8'))
            apply_migrations(conn)
    with connect() as conn:
        begin_write(conn)
        old_versions = {row['user_id']: row['auth_version'] + 1 for row in conn.execute(
            "SELECT user_id,auth_version FROM app_user WHERE user_id IN ('DEMO_admin','DEMO_worker01','DEMO_worker02')")}
        if conn.execute("SELECT 1 FROM candidate_event WHERE SUBSTR(event_id,1,5)='DEMO_'").fetchone() and not reset:
            raise ValueError('Demo already exists; use --reset in the dedicated demo DB')
        media = conn.execute("SELECT storage_path FROM event_media WHERE SUBSTR(event_id,1,5)='DEMO_'").fetchall()
        for row in media:
            if row[0]:
                path = owned_path(row[0])
                if not path.name.startswith('DEMO_'):
                    raise ValueError('Refusing to remove non-demo media')
                path.unlink(missing_ok=True)
        conn.execute("DELETE FROM processing_job WHERE media_id IN (SELECT media_id FROM event_media WHERE SUBSTR(event_id,1,5)='DEMO_')")
        conn.execute("DELETE FROM event_retention_history WHERE SUBSTR(event_id,1,5)='DEMO_'")
        conn.execute("DELETE FROM candidate_event WHERE SUBSTR(event_id,1,5)='DEMO_'")
        conn.execute("DELETE FROM false_positive_aggregate WHERE zone_name IN (?,?)", ZONES)
        conn.execute("DELETE FROM app_user WHERE user_id IN ('DEMO_admin','DEMO_worker01','DEMO_worker02')")
        for zone in ZONES:
            conn.execute('''INSERT INTO zone_rule(zone_name,required_ppe,rules) VALUES(?,'["helmet","vest"]','["시연용 안전 안내"]')
                ON CONFLICT(zone_name) DO NOTHING''', (zone,))
        for index, zone in enumerate(ZONES, 1):
            conn.execute('''INSERT INTO camera_info(camera_id,camera_location,zone_name,status)
                VALUES(?,?,?,'active') ON CONFLICT(camera_id) DO NOTHING''', (f'DEMO_CAM_{index}', f'DEMO 카메라 {index}', zone))
    create_user('DEMO_admin', password, 'DEMO 관리자', 'admin')
    create_user('DEMO_worker01', password, 'DEMO 다중 구역 작업자', 'worker')
    create_user('DEMO_worker02', password, 'DEMO 단일 구역 작업자', 'worker')
    with connect() as conn:
        for user_id, version in old_versions.items():
            conn.execute('UPDATE app_user SET auth_version=? WHERE user_id=?', (version, user_id))
        replace_user_zones(conn, 'DEMO_worker01', ZONES)
        replace_user_zones(conn, 'DEMO_worker02', [ZONES[1]])
    PROCESSED_ROOT.mkdir(parents=True, exist_ok=True)
    for index, (state, day) in enumerate(SPECS, 1):
        event = f'DEMO_EVT_{index:02d}'
        camera = f'DEMO_CAM_{1 if index % 2 else 2}'
        insert_candidate_event({'event_id': event, 'camera_id': camera, 'tracking_id': f'DEMO_TRACK_{index}',
            'ppe_type': 'helmet' if index % 2 else 'vest', 'timestamp_start': day+' 10:00:00', 'timestamp_end': day+' 10:00:01',
            'duration_sec': 1, 'frame_sample_count': 12, 'thumbnail_path': '', 'video_clip_path': '', 'ai_confidence': .9,
            'person_detected': 0, 'ppe_detected': 0, 'model_version': 'DEMO_ONLY_NOT_AI', 'event_status': 'pending'})
        if state not in ('unreviewable','processing','failed'):
            fixtures = [('image', camera)]
            if index == 2:
                fixtures.append(('video', camera))
            if index == 3:
                fixtures.append(('image', 'DEMO_CAM_2'))
            for number, (kind, media_camera) in enumerate(fixtures, 1):
                ident = f'DEMO_MEDIA_{index:02d}_{number}'
                suffix = '.jpg' if kind == 'image' else '.mp4'
                path = PROCESSED_ROOT / (ident + suffix)
                publish_file(ASSETS / ('sample'+suffix), path)
                with connect() as conn:
                    conn.execute('''INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,created_at,inference_backend)
                        VALUES(?,?,?,?,'reference',?,'ready','complete',?,'demo-no-person')''', (ident,event,media_camera,kind,storage_key(path),day+' 10:00:01'))
        elif state in ('processing','failed'):
            with connect() as conn:
                conn.execute('''INSERT INTO event_media(media_id,event_id,camera_id,kind,role,status,redaction_status,created_at,error_code)
                    VALUES(?,?,?,'video','clip',?,?,?,?)''', (f'DEMO_MEDIA_{index:02d}',event,camera,
                    state,'unprocessed' if state=='processing' else 'failed',day+' 10:00:01', None if state=='processing' else 'gpu_unavailable'))
        if state in ('confirmed','false_positive','hold','unreviewable'):
            insert_event_review({'review_id':'DEMO_REVIEW_'+str(index), 'event_id':event, 'reviewer_id':'DEMO_admin',
                'review_result':state, 'review_reason_code':'clip_unavailable' if state == 'unreviewable' else 'demo_'+state, 'review_time':day+' 10:01:00',
                'review_comment':'DEMO: 실제 AI 판정이 아닌 시연 기록', 'confirmed_violation':int(state=='confirmed'), 'second_review_needed':0})
    for quarter in ('2026-Q2','2026-Q3','2026-Q4'):
        generate_quarterly_stats(quarter)
        generate_education_recommendations(quarter)
    return {'demo': True, 'events': len(SPECS), 'users': ['DEMO_admin','DEMO_worker01','DEMO_worker02']}


def main():
    parser = argparse.ArgumentParser(description='Create DEMO-only data in a dedicated DB; no GPU required')
    parser.add_argument('--reset', action='store_true')
    args = parser.parse_args()
    password = os.environ.get('PPE_DEMO_PASSWORD') or getpass('Demo account password (8+ characters): ')
    print('DEMO ONLY: 자료는 얼굴 없는 그림이며 실제 탐지 결과가 아닙니다.')
    print(seed_demo(password, reset=args.reset))


if __name__ == '__main__':
    main()
