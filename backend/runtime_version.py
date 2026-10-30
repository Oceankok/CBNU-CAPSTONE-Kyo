"""Read-only host/server revision comparison; never update sources or start workers."""
import json
import os
from pathlib import Path
import re
import subprocess


def revision(value):
    return value if value and re.fullmatch(r'[0-9a-f]{40}', value) else None


def image_revision():
    return revision(os.environ.get('PPE_BUILD_REVISION'))


def local_version(root=None):
    root = root or Path(__file__).resolve().parents[1]
    try:
        commit = subprocess.run(['git', 'rev-parse', '--verify', 'HEAD'], cwd=root,
            check=True, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5).stdout.strip()
        status = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=normal'], cwd=root,
            check=True, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5).stdout
        return {'revision': revision(commit), 'dirty': bool(status.strip())}
    except (OSError, subprocess.SubprocessError):
        return {'revision': None, 'dirty': None}


def compare(local, server):
    schema_ready = server.get('database_schema_version') == server.get('required_schema_version') \
        and isinstance(server.get('database_schema_version'), int)
    host, deployed = revision(local.get('revision')), revision(server.get('revision'))
    if not schema_ready:
        status = 'schema_mismatch'
    elif not host or not deployed or local.get('dirty') is None:
        status = 'unknown'
    elif local['dirty']:
        status = 'dirty'
    else:
        status = 'matched' if host == deployed else 'different'
    return {'status': status, 'host_revision': host, 'host_dirty': local.get('dirty'),
            'server_revision': deployed, 'schema_ready': schema_ready}


def main():
    import httpx
    from inference_worker.video_client import VideoClient
    client = None
    try:
        client = VideoClient()
        result = compare(local_version(), client.get_runtime_version())
    except (httpx.HTTPError, RuntimeError, ValueError):
        # Do not print a response, configured URL, token, or authentication material.
        result = {'status': 'server_version_unavailable'}
    finally:
        if client:
            client.close()
    print(json.dumps(result))
    raise SystemExit(0 if result['status'] == 'matched' else 2)


if __name__ == '__main__':
    main()
