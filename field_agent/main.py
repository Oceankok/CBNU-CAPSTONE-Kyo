"""Run on the PC connected to the loudspeaker; no database access required."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import time

import httpx

from backend.services.tts_service import speak_message


def execute(command, allow_language_install=False):
    if time.time() >= command["expires_at"]:
        return {"status": "skipped", "result": {"reason": "expired"}}
    if command["kind"] != "broadcast":
        return {"status": "failed", "result": {"reason": "unsupported_command"}}
    payload = command["payload"]
    result = speak_message(payload["message"], payload["language"], payload.get("voice_id"))
    return {"status": "completed" if result["spoken"] else "failed", "result": result}


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def run(server, token, state_path, once=False, allow_language_install=False):
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    # A crash may have happened after speaking. Report uncertainty rather than repeat.
    for record in state.values():
        if record["status"] == "started":
            record.update(status="failed", result={"reason": "agent_restarted_execution_unknown"})
    task = None
    command_id = None
    last_heartbeat = 0
    with httpx.Client(base_url=server.rstrip("/"), headers={"Authorization": "Bearer "+token}, timeout=10) as client:
        with ThreadPoolExecutor(max_workers=1) as executor:
            while True:
                try:
                    if time.monotonic()-last_heartbeat >= 10:
                        response = client.post("/api/field/heartbeat")
                        response.raise_for_status()
                        last_heartbeat = time.monotonic()
                    if task is not None and task.done():
                        try:
                            state[command_id] = task.result()
                        except Exception as exc:
                            state[command_id] = {"status": "failed", "result": {"reason": "execution_error", "error": str(exc)}}
                        save_state(state_path, state)
                        task = None
                    for key, record in list(state.items()):
                        if record["status"] == "started":
                            continue
                        response = client.put(f"/api/field/commands/{key}/result", json=record)
                        if response.status_code in {404, 409}:
                            print("Result no longer accepted:", key, response.status_code)
                        else:
                            response.raise_for_status()
                        del state[key]
                        save_state(state_path, state)
                    if once and command_id is not None and task is None and not state:
                        return
                    if task is None and not state:
                        response = client.post("/api/field/commands/claim")
                        response.raise_for_status()
                        command = response.json()["command"]
                        if command is None and once:
                            return
                        if command is not None:
                            command_id = command["command_id"]
                            state[command_id] = {"status": "started"}
                            save_state(state_path, state)
                            task = executor.submit(execute, command, allow_language_install)
                except httpx.HTTPError as exc:
                    print("Server request failed:", type(exc).__name__)
                    if once:
                        raise
                time.sleep(2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://localhost:8000")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--allow-language-install", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("PPE_NODE_TOKEN")
    if not token:
        raise SystemExit("Set PPE_NODE_TOKEN to the token returned by node registration")
    identity = hashlib.sha256((args.server+token).encode()).hexdigest()[:16]
    try:
        run(args.server, token, Path("field_state") / (identity+".json"), args.once, args.allow_language_install)
    except KeyboardInterrupt:
        print("Field agent stopped")


if __name__ == "__main__":
    main()
