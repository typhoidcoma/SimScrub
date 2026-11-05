"""TE SimScrub local server.

Thin layer over the pipeline: holds the current job, runs stages in the
background, and pushes the whole manifest to the UI over a WebSocket after
every change. The manifest is small, so full snapshots keep the UI dumb.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from simscrub import pipeline
from simscrub.manifest import Job, LogLine

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "jobs"

app = FastAPI(title="TE SimScrub")


VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}


def _initial_job() -> Job:
    """Reopen the last job so a server restart doesn't lose the artist's work."""
    path = JOBS / "office" / "manifest.json"
    return Job.load(path) if path.exists() else Job(id="office", name="Home office")


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.job: Job = _initial_job()
        self.task: asyncio.Task | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._last_push = 0.0

    def push_from_thread(self, job: Job) -> None:
        """Pipeline callback from the worker thread; throttled to ~12 updates/s."""
        now = time.monotonic()
        settled = all(st.status.value != "working" for st in job.stages.values())
        if not settled and now - self._last_push < 0.08:
            return
        self._last_push = now
        snapshot = job.model_copy(deep=True)
        asyncio.run_coroutine_threadsafe(self.broadcast(snapshot, save=False), self.loop)

    @property
    def job_dir(self) -> Path:
        return JOBS / self.job.id

    async def broadcast(self, job: Job, save: bool = True) -> None:
        self.job = job
        if save:
            job.save(self.job_dir / "manifest.json")
        data = job.to_json()
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json({"type": "job", "job": data})
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()


hub = Hub()


def _run_pipeline(job: Job, job_dir: Path, video: Path, display_name: str) -> None:
    try:
        frames = pipeline.run_capture(job, job_dir, video, hub.push_from_thread)
        job.capture.video = display_name
        pipeline.run_segment(job, job_dir, video, hub.push_from_thread, frames=frames)
    except Exception:  # noqa: BLE001 - stage already shows a plain-language chip
        job.log.append(LogLine(t=0, stage="error", text=traceback.format_exc(limit=3)))
        job.save(job_dir / "manifest.json")
        hub.push_from_thread(job)


async def _start(video: Path, job_id: str, display_name: str) -> None:
    hub.loop = asyncio.get_running_loop()
    job_dir = JOBS / job_id
    job = Job(id=job_id, name="Home office")
    hub.job = job
    hub.task = asyncio.create_task(asyncio.to_thread(_run_pipeline, job, job_dir, video, display_name))


@app.post("/api/process")
async def process(video: UploadFile | None = File(None), path: str | None = None, job: str = "office") -> dict:
    """Start capture + segment from an uploaded video or a video in the project folder."""
    if hub.busy:
        raise HTTPException(409, "A run is already in progress.")
    job_dir = JOBS / job
    job_dir.mkdir(parents=True, exist_ok=True)
    # Clear the previous source. Phone videos are often read-only, and a copy
    # that inherits that flag can't be overwritten on the next run.
    for old in job_dir.glob("source.*"):
        old.chmod(0o666)
        old.unlink()
    if video is not None and video.filename:
        ext = Path(video.filename).suffix.lower()
        if ext not in VIDEO_EXT:
            raise HTTPException(400, "That file isn't a video. Try an MP4 or MOV from your phone.")
        dest = job_dir / f"source{ext}"
        with dest.open("wb") as out:
            shutil.copyfileobj(video.file, out)
        name = video.filename
    elif path:
        src = (ROOT / path).resolve()
        if ROOT not in src.parents or not src.is_file() or src.suffix.lower() not in VIDEO_EXT:
            raise HTTPException(400, "Couldn't find that video in the project folder.")
        dest = job_dir / f"source{src.suffix.lower()}"
        shutil.copyfile(src, dest)  # contents only, not the read-only flag
        name = src.name
    else:
        raise HTTPException(400, "Drop a walkthrough video first.")
    await _start(dest, job, name)
    return {"ok": True}


@app.get("/api/videos")
def list_videos() -> list[str]:
    """Videos sitting in the project folder, offered as one-click inputs."""
    return sorted(p.name for p in ROOT.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT)


@app.get("/api/jobs/{job_id}/files/{rel:path}")
def job_file(job_id: str, rel: str) -> FileResponse:
    base = (JOBS / job_id).resolve()
    target = (base / rel).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "File not found.")
    return FileResponse(target, headers={"Cache-Control": "no-cache"})


@app.get("/api/job")
def get_job() -> dict:
    return hub.job.to_json()


@app.post("/api/assets/{asset_id}/exclude")
async def toggle_exclude(asset_id: str) -> dict:
    for a in hub.job.assets:
        if a.id == asset_id:
            a.excluded = not a.excluded
            await hub.broadcast(hub.job)
            return {"ok": True, "excluded": a.excluded}
    raise HTTPException(404, f"No asset {asset_id}.")


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    await socket.accept()
    hub.clients.add(socket)
    await socket.send_json({"type": "job", "job": hub.job.to_json()})
    try:
        while True:
            await socket.receive_text()  # keepalive; commands go over REST
    except WebSocketDisconnect:
        hub.clients.discard(socket)
