"""Manual Docker release snapshots and isolated restore rehearsal (stdlib only)."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

FILES = ('postgres.dump','media.tar','media.json')
IMAGE = re.compile(r'^ghcr\.io/[a-z0-9_./-]+@sha256:[0-9a-f]{64}$')


def docker_path():
    found = shutil.which('docker')
    if found:
        return found
    path = Path(os.environ.get('LOCALAPPDATA',''))/'Programs/DockerDesktop/resources/bin/docker.exe'
    if not path.is_file():
        raise RuntimeError('Docker executable unavailable')
    return str(path)


def run(arguments, *, env=None, stdin=None, stdout=None):
    result = subprocess.run([docker_path(),*arguments],env=env,stdin=stdin,stdout=stdout or subprocess.PIPE,
                            stderr=subprocess.PIPE,timeout=600)
    if result.returncode:
        # Do not echo commands, environment settings, or container error data containing credentials.
        raise RuntimeError('Docker snapshot operation failed; check container status locally')
    return result.stdout or b''


def sha(path):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file,'sha256').hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def verify_bundle(folder):
    folder = Path(folder).resolve()
    manifest = folder/'bundle.json'
    if manifest.is_symlink() or manifest.stat().st_size > 65536:
        raise ValueError('Unsafe snapshot manifest')
    body = json.loads(manifest.read_text(encoding='utf-8-sig'))
    if body['format'] != 1 or not all(isinstance(body[key], (int, float)) and math.isfinite(body[key]) for key in ('created_at','expires_at')) or not body['created_at'] <= time.time() < body['expires_at'] <= body['created_at']+86400 or not IMAGE.fullmatch(body['release']['api_image']):
        raise ValueError('Expired or unsupported backup')
    if set(body['sha256']) != set(FILES):
        raise ValueError('Incomplete backup file set')
    for name in FILES:
        file = folder/name
        if file.is_symlink() or not file.is_file() or sha(file) != body['sha256'][name]:
            raise ValueError('Snapshot checksum mismatch')
    return body


@contextmanager
def release_context(root):
    root = Path(root).resolve()
    if root.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('Deployment root must be outside checkout')
    release = json.loads((root/'current-release.json').read_text(encoding='utf-8-sig'))
    if not re.fullmatch(r'[0-9a-f]{40}',release['commit']) or not re.fullmatch(r'[a-z][a-z0-9-]{2,40}',release['project']):
        raise ValueError('Invalid release record')
    if not all(IMAGE.fullmatch(release[key]) for key in ('api_image','web_image')):
        raise ValueError('Published image digests required')
    base = ['compose','--project-name',release['project'],'--env-file',str(root/'server.env'),
            '-f',str(root/'releases'/release['commit']/'compose.release.yaml')]
    if release['https']:
        base += ['--profile','https']
    env = dict(os.environ,PPE_API_IMAGE=release['api_image'],PPE_WEB_IMAGE=release['web_image'],
               PPE_WEB_BIND='0.0.0.0' if release.get('lan') else '127.0.0.1')
    lock = root/'release.lock'
    with lock.open('x') as file:
        file.write(str(os.getpid()))
        file.flush()
        try:
            yield root,release,base,env
        finally:
            # Close the Windows handle before unlinking.
            file.close()
            lock.unlink()


def backup(root, allow_pause):
    if not allow_pause:
        raise ValueError('Explicit --allow-service-pause required')
    with release_context(root) as (root,release,base,env):
        running = run(base+['ps','--status','running','--services'],env=env).decode().splitlines()
        if not {'api','postgres'}.issubset(set(running)):
            raise ValueError('Running release API/PostgreSQL required')
        writers = [service for service in ('gateway','web','api','maintenance') if service in running]
        folder = root/'backups'/('snapshot-'+uuid4().hex)
        folder.mkdir(parents=True,exist_ok=False)
        complete = False
        try:
            # Finally restarts the original services even when stop/export fails.
            run(base+['stop','--timeout','30',*writers],env=env)
            with (folder/'postgres.dump').open('xb') as file:
                run(base+['exec','-T','postgres','pg_dump','-U','kyo','-d','kyo','-Fc'],env=env,stdout=file)
            run(base+['run','--rm','--no-deps','--volume',str(folder)+':/backup','api',
                      'python','-m','backend.backup.media','export','--directory','/backup'],env=env)
            media = json.loads((folder/'media.json').read_text(encoding='utf-8'))
            write_json(folder/'bundle.json',{'format':1,'created_at':media['created_at'],'expires_at':media['expires_at'],
                       'release':release,'sha256':{name:sha(folder/name) for name in FILES}})
            verify_bundle(folder)
            complete = True
        finally:
            try:
                run(base+['start','--wait','--wait-timeout','180',*writers],env=env)
            finally:
                if not complete:
                    for name in (*FILES,'bundle.json'):
                        (folder/name).unlink(missing_ok=True)
                    folder.rmdir()
        return {'backup_directory':str(folder),'archived_media':len(media['files']),'expires_at':media['expires_at']}


def restore_check(folder):
    folder = Path(folder).resolve()
    body = verify_bundle(folder)
    ident = uuid4().hex
    project, database = 'kyo-restore-'+ident, 'kyo_restore_'+ident
    env = dict(os.environ,PPE_RESTORE_PASSWORD=secrets.token_urlsafe(32))
    env['PPE_RESTORE_URL']='postgresql://kyo:'+env['PPE_RESTORE_PASSWORD']+'@postgres:5432/'+database
    with tempfile.TemporaryDirectory(prefix='kyo-restore-compose-') as temporary:
        config = Path(temporary)/'restore.json'
        spec = {'services':{
            'postgres':{'image':'postgres:17','environment':{'POSTGRES_DB':database,'POSTGRES_USER':'kyo',
                         'POSTGRES_PASSWORD':'${PPE_RESTORE_PASSWORD}'},'volumes':['db:/var/lib/postgresql/data'],
                         'healthcheck':{'test':['CMD-SHELL','pg_isready -U kyo -d '+database],'interval':'2s','timeout':'2s','retries':30}},
            'check':{'image':body['release']['api_image'],'environment':{'PPE_DATABASE_URL':'${PPE_RESTORE_URL}',
                      'PPE_PROCESSING_MODE':'disabled'},'volumes':['media:/app/storage','private:/app/.private_media'],
                      'healthcheck':{'disable':True}}},'volumes':{'db':{},'media':{},'private':{}}}
        write_json(config,spec)
        empty = Path(temporary)/'empty.env'
        empty.write_text('')
        base = ['compose','--project-name',project,'--env-file',str(empty),'-f',str(config)]
        # Refuse any pre-existing resources before cleanup can ever run.
        label = 'label=com.docker.compose.project='+project
        if run(['ps','-aq','--filter',label],env=env).strip() or run(['volume','ls','-q','--filter',label],env=env).strip():
            raise ValueError('Restore project already exists')
        try:
            run(base+['up','-d','--wait','--wait-timeout','120','postgres'],env=env)
            with (folder/'postgres.dump').open('rb') as file:
                run(base+['exec','-T','postgres','pg_restore','-U','kyo','-d',database,
                          '--no-owner','--no-privileges','--exit-on-error'],env=env,stdin=file)
            output = run(base+['run','--rm','--no-deps','--volume',str(folder)+':/backup:ro','check',
                              'python','-m','backend.backup.media','restore','--directory','/backup'],env=env)
            result = json.loads(output)
            return {'isolated_restore':'PASS',**result}
        finally:
            if not re.fullmatch(r'kyo-restore-[0-9a-f]{32}',project):
                raise ValueError('Unsafe restore cleanup project')
            run(base+['down','--volumes','--remove-orphans'],env=env)


def prune(root, apply):
    removed = []
    for folder in (Path(root).resolve()/'backups').glob('snapshot-*'):
        if folder.is_symlink() or not folder.is_dir() or not re.fullmatch(r'snapshot-[0-9a-f]{32}',folder.name):
            continue
        try:
            body = json.loads((folder/'bundle.json').read_text(encoding='utf-8-sig'))
            if body['format'] != 1 or not math.isfinite(body['expires_at']) or body['expires_at'] > time.time():
                continue
            if set(p.name for p in folder.iterdir()) != {*FILES,'bundle.json'} or any(p.is_symlink() for p in folder.iterdir()):
                continue
        except (ValueError,KeyError,OSError):
            continue
        removed.append(folder.name)
        if apply:
            for name in (*FILES,'bundle.json'):
                (folder/name).unlink()
            folder.rmdir()
    return {'expired_snapshots':removed,'deleted':apply}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation',required=True)
    create = commands.add_parser('backup')
    create.add_argument('--deployment-root',type=Path,required=True)
    create.add_argument('--allow-service-pause',action='store_true')
    for name in ('verify','restore-check'):
        child = commands.add_parser(name)
        child.add_argument('--backup',type=Path,required=True)
    clean = commands.add_parser('prune')
    clean.add_argument('--deployment-root',type=Path,required=True)
    clean.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    if args.operation == 'backup': result = backup(args.deployment_root,args.allow_service_pause)
    elif args.operation == 'verify': result = {'verified':bool(verify_bundle(args.backup))}
    elif args.operation == 'restore-check': result = restore_check(args.backup)
    else: result = prune(args.deployment_root,args.apply)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
