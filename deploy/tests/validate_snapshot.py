"""Opt-in Docker roundtrip. Run only after approval; all resources are disposable."""
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deploy import snapshot


def main():
    image = 'kyo-api:backup-validation'
    project = 'kyo-snapshot-test-'+uuid4().hex[:10]
    env = dict(os.environ)
    env['PATH'] = str(Path(snapshot.docker_path()).parent)+os.pathsep+env.get('PATH','')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    def docker(args):
        return snapshot.run(args,env=env)
    print('Building isolated API image...',flush=True)
    docker(['build','-f',str(ROOT/'deploy/api.Dockerfile'),'-t',image,str(ROOT)])
    with tempfile.TemporaryDirectory(prefix='kyo-snapshot-roundtrip-') as directory:
        root = Path(directory)
        commit = 'a'*40
        release = root/'releases'/commit
        release.mkdir(parents=True)
        shutil.copyfile(ROOT/'compose.release.yaml',release/'compose.release.yaml')
        password = secrets.token_urlsafe(30)
        (root/'server.env').write_text('\n'.join([
            'PPE_API_IMAGE='+image,'PPE_WEB_IMAGE=kyo-web:backup-validation',
            'PPE_DATABASE_URL=postgresql://kyo:'+password+'@postgres:5432/kyo',
            'PPE_POSTGRES_PASSWORD='+password,'PPE_JWT_SECRET='+secrets.token_urlsafe(40),
            'PPE_API_PORT='+str(port),'PPE_PROCESSING_MODE=disabled'])+'\n')
        snapshot.write_json(root/'current-release.json',{'commit':commit,'project':project,'api_image':image,
                            'web_image':'kyo-web:backup-validation','https':False,'lan':False})
        base = ['compose','--project-name',project,'--env-file',str(root/'server.env'),'-f',str(release/'compose.release.yaml')]
        label = 'label=com.docker.compose.project='+project
        if docker(['ps','-aq','--filter',label]).strip() or docker(['volume','ls','-q','--filter',label]).strip():
            raise RuntimeError('Test project already exists')
        try:
            docker(base+['up','-d','--wait','--wait-timeout','120','postgres'])
            fixture = """
import os,time
from pathlib import Path
os.environ['PPE_DB_SEED']='true'
from backend.db.init_db import init_database
from backend.db.event_repository import get_connection
from backend.auth.service import create_user
init_database()
create_user('snapshot_admin','only-test-password','Snapshot','admin')
path=Path('/app/storage/candidate_events/processed/retained.jpg')
path.parent.mkdir(parents=True,exist_ok=True)
path.write_bytes(b'synthetic-redacted-image')
Path('/app/.private_media/excluded-original').write_bytes(b'must-not-be-restored')
with get_connection() as conn:
    camera=conn.execute("SELECT camera_id FROM candidate_event WHERE event_id='EVT_0001'").fetchone()[0]
    conn.execute("INSERT INTO event_media(media_id,event_id,camera_id,kind,role,storage_path,status,redaction_status,source_path,created_at) VALUES('roundtrip','EVT_0001',?,'image','reference','storage/candidate_events/processed/retained.jpg','ready','complete','excluded-original',CURRENT_TIMESTAMP)",(camera,))
    conn.execute("UPDATE event_retention SET decision='retain',consent_confirmed=1,retain_until=? WHERE event_id='EVT_0001'",(time.time()+3600,))
    conn.execute("INSERT INTO field_node(node_id,name,token_hash) VALUES('test_node','Test node','test-hash')")
    conn.execute("INSERT INTO field_command(command_id,node_id,kind,payload,created_at,expires_at) VALUES('test_command','test_node','broadcast','{}',0,9999999999)")
"""
            docker(base+['run','--rm','--no-deps','api','python','-c',fixture])
            docker(base+['up','-d','--wait','--wait-timeout','120','api','maintenance'])
            # Only this test process accepts these local validation tags. Production
            # snapshot commands continue to require immutable published GHCR digests.
            image_pattern = re.compile(r'^kyo-(api|web):backup-validation$')
            with patch.object(snapshot,'IMAGE',image_pattern), patch.dict(os.environ,env):
                result = snapshot.backup(root,True)
                folder = Path(result['backup_directory'])
                assert result['archived_media']==1
                print('PASS: pg_dump + retained media exported; source services restarted',flush=True)
                running = docker(base+['ps','--status','running','--services']).decode().splitlines()
                assert {'api','maintenance','postgres'}.issubset(set(running))
                original_run = snapshot.run
                def observe(args,**kwargs):
                    output = original_run(args,**kwargs)
                    if 'backend.backup.media' in args and 'restore' in args:
                        check_base = args[:args.index('run')]
                        assertions = """
from pathlib import Path
from backend.db.event_repository import get_connection
assert Path('/app/storage/candidate_events/processed/retained.jpg').read_bytes()==b'synthetic-redacted-image'
assert not Path('/app/.private_media/excluded-original').exists()
with get_connection() as conn:
    assert conn.execute("SELECT is_active FROM field_node WHERE node_id='test_node'").fetchone()[0]==0
    assert conn.execute("SELECT status FROM field_command WHERE command_id='test_command'").fetchone()[0]=='skipped'
    assert conn.execute("SELECT auth_version FROM app_user WHERE user_id='snapshot_admin'").fetchone()[0]==1
    assert conn.execute("SELECT source_path FROM event_media WHERE media_id='roundtrip'").fetchone()[0]==''
"""
                        original_run(check_base+['run','--rm','--no-deps','check','python','-c',assertions],env=kwargs['env'])
                    return output
                with patch.object(snapshot,'run',side_effect=observe):
                    report = snapshot.restore_check(folder)
                assert report['isolated_restore']=='PASS' and report['restored_media']==1
                print('PASS: real pg_restore + exact media bytes; original excluded; commands disabled; auth invalidated',flush=True)
            # Both operations leave the source DB and retained file intact.
            docker(base+['exec','-T','api','python','-c',"from pathlib import Path; from backend.db.event_repository import get_connection; assert Path('/app/storage/candidate_events/processed/retained.jpg').is_file(); c=get_connection(); assert c.execute(\"SELECT is_active FROM field_node WHERE node_id='test_node'\").fetchone()[0]==1; c.close()"])
            print('PASS: source DB/media unchanged; isolated restore resources cleaned',flush=True)
        finally:
            if not re.fullmatch(r'kyo-snapshot-test-[0-9a-f]{10}',project):
                raise RuntimeError('Unsafe test cleanup')
            docker(base+['down','--volumes','--remove-orphans'])
    print('SNAPSHOT ROUNDTRIP PASS',flush=True)


if __name__ == '__main__':
    main()
