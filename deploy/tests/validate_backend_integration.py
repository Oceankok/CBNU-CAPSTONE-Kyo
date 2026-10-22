"""Build current API/web and run an HTTP journey on disposable Docker PostgreSQL.

No production URL, credentials or volume may be passed to this command.
It never starts Docker Desktop or changes the user's deployment.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from deploy.snapshot import docker_path, run
from backend.tests.integration_scenario import request, run_scenario


def main():
    import httpx
    ident = uuid4().hex[:12]
    project = 'kyo-integration-'+ident
    images = [project+'-api:local', project+'-web:local']
    env = dict(os.environ)
    env['PATH'] = str(Path(docker_path()).parent)+os.pathsep+env.get('PATH', '')
    db_password = secrets.token_hex(24)
    env['KYO_TEST_DATABASE_URL'] = 'postgresql://kyo:'+db_password+'@postgres:5432/kyo_test_integration'
    env['KYO_TEST_DB_PASSWORD'] = db_password
    env['KYO_TEST_JWT_SECRET'] = secrets.token_hex(32)
    password = secrets.token_urlsafe(24)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    report = None
    with tempfile.TemporaryDirectory(prefix=project+'-') as temporary:
        folder = Path(temporary)
        config = folder/'compose.json'
        empty = folder/'empty.env'
        empty.write_text('')
        config.write_text(json.dumps({'services': {
            'postgres': {'image': 'postgres:17', 'environment': {'POSTGRES_DB': 'kyo_test_integration', 'POSTGRES_USER': 'kyo', 'POSTGRES_PASSWORD': '${KYO_TEST_DB_PASSWORD}'},
                         'volumes': ['db:/var/lib/postgresql/data'], 'healthcheck': {'test': ['CMD-SHELL', 'pg_isready -U kyo -d kyo_test_integration'], 'interval': '2s', 'timeout': '2s', 'retries': 30}},
            'api': {'image': images[0], 'build': {'context': str(ROOT), 'dockerfile': 'deploy/api.Dockerfile'},
                    'environment': {'PPE_DATABASE_URL': '${KYO_TEST_DATABASE_URL}', 'PPE_JWT_SECRET': '${KYO_TEST_JWT_SECRET}',
                                    'PPE_PROCESSING_MODE': 'remote', 'PPE_LOCAL_PROCESSING': 'false', 'PPE_COOKIE_SECURE': 'false', 'PPE_EQUIPMENT_PROVIDER': ''},
                    'volumes': ['media:/app/storage', 'private:/app/.private_media'], 'depends_on': {'postgres': {'condition': 'service_healthy'}},
                    'healthcheck': {'test': ['CMD', 'python', '-c', "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=3).close()"], 'interval': '2s', 'timeout': '5s', 'retries': 30}},
            'web': {'image': images[1], 'build': {'context': str(ROOT), 'dockerfile': 'deploy/web.Dockerfile'},
                    'ports': [f'127.0.0.1:{port}:8080'], 'depends_on': {'api': {'condition': 'service_healthy'}},
                    'healthcheck': {'test': ['CMD', 'wget', '-q', '-O', '/dev/null', 'http://127.0.0.1:8080/nginx-health'], 'interval': '2s', 'timeout': '3s', 'retries': 30}}},
            'volumes': {'db': {}, 'media': {}, 'private': {}}}), encoding='utf-8')
        base = ['compose', '--project-name', project, '--env-file', str(empty), '-f', str(config)]

        def docker(args, **kwargs):
            return run(args, env=env, **kwargs)

        label = 'label=com.docker.compose.project='+project
        if docker(['ps', '-aq', '--filter', label]).strip() or docker(['volume', 'ls', '-q', '--filter', label]).strip():
            raise RuntimeError('Disposable project already exists')
        try:
            print('Building disposable API/web images', flush=True)
            docker(base+['build', 'api', 'web'], stdout=sys.stdout.buffer)
            docker(base+['up', '-d', '--wait', '--wait-timeout', '120', 'postgres'])
            bootstrap = """
import sys
from backend.db.init_db import init_database
from backend.auth.service import create_user
init_database()
password=sys.stdin.read()
create_user('admin02',password,'Integration administrator','admin')
create_user('worker01',password,'Integration worker','worker')
"""
            with tempfile.TemporaryFile() as credential:
                credential.write(password.encode())
                credential.seek(0)
                docker(base+['run', '--rm', '--no-deps', '-T', 'api', 'python', '-c', bootstrap], stdin=credential)
            docker(base+['up', '-d', '--wait', '--wait-timeout', '120', 'api', 'web'])
            origin = 'http://127.0.0.1:'+str(port)
            factory = lambda: httpx.Client(base_url=origin, timeout=120, trust_env=False)
            with factory() as client:
                request(client, 'GET', '/health/ready')
                assert 'text/html' in request(client, 'GET', '/').headers['content-type']
            report = run_scenario(factory, password)
            print('PASS: HTTP journey through web reverse proxy', flush=True)
            inspect = """
from pathlib import Path
from backend.db.event_repository import get_connection
from backend.media.paths import PROCESSED_ROOT,SOURCE_ROOT
with get_connection() as c:
    assert c.execute('SELECT COUNT(*) FROM event_merge').fetchone()[0]==1
    assert c.execute("SELECT COUNT(*) FROM event_media WHERE status='deleted'").fetchone()[0]==4
    assert c.execute("SELECT COUNT(*) FROM consent_record WHERE status='revoked'").fetchone()[0]==1
assert not list(PROCESSED_ROOT.glob('*.mp4'))
assert not list(SOURCE_ROOT.glob('*'))
"""
            docker(base+['exec', '-T', 'api', 'python', '-c', inspect])
            docker(base+['exec', '-T', 'api', 'python', '-m', 'backend.media.maintenance', '--once'])
            docker(base+['up', '-d', '--force-recreate', '--wait', '--wait-timeout', '120', 'api', 'web'])
            with factory() as client:
                login = request(client, 'POST', '/api/auth/login', json={'user_id': 'admin02', 'password': password}).json()
                client.headers['Authorization'] = 'Bearer '+login['access_token']
                events = request(client, 'GET', '/api/events').json()['items']
                assert len(events) == 1 and len(events[0]['merge_history']) == 1
                assert all(item['status'] == 'deleted' for item in events[0]['media'])
                assert len(request(client, 'GET', '/api/camera-groups').json()['items']) == 1
            docker(base+['exec', '-T', 'api', 'python', '-c', inspect])
            report.update({'docker_postgresql': True, 'web_proxy': True, 'container_recreation_persistence': True,
                           'physical_file_deletion': True, 'maintenance': True, 'production_changed': False})
        finally:
            if project != 'kyo-integration-'+ident:
                raise RuntimeError('Unsafe integration cleanup target')
            docker(base+['down', '--volumes', '--remove-orphans'])
            # Only uniquely named images built by this invocation are eligible for removal.
            for image in images:
                if docker(['image', 'ls', '-q', image]).strip():
                    docker(['image', 'rm', image])
    output = ROOT/'.private_media/backend-integration-results.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS: isolated Docker integration, persistence and cleanup', flush=True)


if __name__ == '__main__':
    main()
