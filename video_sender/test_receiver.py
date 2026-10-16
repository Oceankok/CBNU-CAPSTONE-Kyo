
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI()

SAVE_DIR = Path("video_sender/received")
SAVE_DIR.mkdir(parents=True, exist_ok=True)

streams = {}


@app.post("/api/video/streams")
async def create_stream(request: Request):
    data = await request.json()
    camera_id = data.get("camera_id")

    if not camera_id:
        raise HTTPException(status_code=400, detail="camera_id required")

    stream_id = str(uuid4())
    streams[stream_id] = camera_id

    print(f"[STREAM] {camera_id}: {stream_id}")
    return {"stream_id": stream_id}


@app.post("/api/video/streams/{stream_id}/chunks")
async def receive_chunk(
    stream_id: str,
    sequence: int,
    request: Request,
    x_duration_ms: int = Header(default=1000),
):
    if stream_id not in streams:
        raise HTTPException(status_code=404, detail="Unknown stream")

    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="Empty video chunk")

    camera_id = streams[stream_id]
    camera_dir = SAVE_DIR / camera_id
    camera_dir.mkdir(parents=True, exist_ok=True)

    output = camera_dir / f"chunk_{sequence:05d}.mp4"
    output.write_bytes(content)

    print(f"[RECEIVED] {camera_id}, seq={sequence}, bytes={len(content)}")

    return JSONResponse(
        status_code=202,
        content={"received": True, "sequence": sequence},
    )
