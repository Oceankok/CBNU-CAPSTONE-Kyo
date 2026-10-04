"""Verify real HTTP between a temporary server and a separate field-agent process.

python scripts/verify_field_processes.py [--audio]
No development DB is used; --audio speaks one brief Korean test message.
"""
import argparse
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", action="store_true")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="ppe-http-test-") as directory:
        env = os.environ.copy()
        env.update(PPE_DB_PATH=str(Path(directory)/"test.db"), PPE_JWT_SECRET=secrets.token_urlsafe(48),
                   PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
        with patch.dict(os.environ, env):
            from backend.db.init_db import init_database
            from backend.auth.service import create_user
            init_database()
            password = secrets.token_urlsafe(20)
            create_user("test-admin", password, "Test", "admin")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with (Path(directory)/"server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "backend.api.main:app", "--host", "127.0.0.1", "--port", str(port)],
                                      cwd=ROOT, env=env, stdout=log, stderr=log, creationflags=flags)
            try:
                with httpx.Client(base_url=base, timeout=5) as client:
                    for _ in range(100):
                        try:
                            client.get("/").raise_for_status()
                            break
                        except httpx.HTTPError:
                            time.sleep(0.1)
                    else:
                        raise RuntimeError("Temporary server did not start")
                    session = client.post("/api/auth/login", json={"user_id": "test-admin", "password": password})
                    session.raise_for_status()
                    client.headers["Authorization"] = "Bearer "+session.json()["access_token"]
                    node = client.post("/api/nodes", json={"node_id": "testpc", "name": "Local field process"})
                    node.raise_for_status()
                    env["PPE_NODE_TOKEN"] = node.json()["token"]

                    def agent_once():
                        completed = subprocess.run([sys.executable, "-B", "-m", "field_agent.main", "--server", base, "--once"],
                                                   cwd=directory, env=env, capture_output=True, text=True, encoding="utf-8",
                                                   timeout=45, creationflags=flags)
                        if completed.returncode:
                            raise RuntimeError(completed.stderr or completed.stdout)

                    client.post("/api/nodes/testpc/voices/refresh").raise_for_status()
                    agent_once()
                    voices = client.get("/api/nodes/testpc/voices").json()["items"]
                    assert voices, "No TTS voices found on field process"
                    print("Separate-process voice scan:", [v["name"] for v in voices])
                    if args.audio:
                        voice = next(v for v in voices if "ko-KR" in v["languages"])
                        client.put("/api/nodes/testpc/voice", json={"language": "ko", "voice_id": voice["id"]}).raise_for_status()
                        requested = client.post("/api/nodes/testpc/test-broadcast", json={"message": "경고 방송 연결 테스트입니다."})
                        requested.raise_for_status()
                        agent_once()
                        rows = client.get("/api/nodes/testpc/commands").json()["items"]
                        result = next(r for r in rows if r["command_id"] == requested.json()["command_id"])
                        assert result["status"] == "completed", result
                        assert result["result"]["spoken"] is True
                        print("Separate-process Korean TTS: completed")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    main()
