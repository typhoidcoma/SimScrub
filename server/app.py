"""TE SimScrub local server.

Thin layer over the pipeline: holds the current job, runs stages in the
background, and pushes the whole manifest to the UI over a WebSocket after
every change. The manifest is small, so full snapshots keep the UI dumb.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response

from simscrub import pipeline
from pydantic import BaseModel

from simscrub.manifest import Dims, Job, LogLine

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "jobs"

app = FastAPI(title="TE SimScrub")


VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}


def _initial_job() -> Job:
    """Reopen the last job so a server restart doesn't lose the artist's work."""
    path = JOBS / "office" / "manifest.json"
    return Job.load(path) if path.exists() else Job(id="office", name="Untitled project")


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
    # Re-processing keeps the artist's project name.
    name = hub.job.name if hub.job.id == job_id else "Untitled project"
    job = Job(id=job_id, name=name)
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


@app.get("/api/reconstruct/plan")
async def reconstruct_plan() -> dict:
    """Cost preview before anything is spent. The balance lookup is free."""
    return await asyncio.to_thread(pipeline.reconstruct_plan, hub.job, hub.job_dir)


@app.post("/api/reconstruct")
async def reconstruct_run() -> dict:
    if hub.busy:
        raise HTTPException(409, "A run is already in progress.")
    if not any(not a.excluded and a.crop for a in hub.job.assets):
        raise HTTPException(400, "No boxes to generate. Run Segment first.")
    hub.loop = asyncio.get_running_loop()
    job, job_dir = hub.job, hub.job_dir

    def run() -> None:
        try:
            pipeline.run_reconstruct(job, job_dir, hub.push_from_thread)
        except Exception:  # noqa: BLE001
            job.log.append(LogLine(t=0, stage="error", text=traceback.format_exc(limit=3)))
            job.save(job_dir / "manifest.json")
            hub.push_from_thread(job)

    hub.task = asyncio.create_task(asyncio.to_thread(run))
    return {"ok": True}


@app.post("/api/simready")
async def simready_run() -> dict:
    if hub.busy:
        raise HTTPException(409, "A run is already in progress.")
    if not any(not a.excluded and a.recon.mesh for a in hub.job.assets):
        raise HTTPException(400, "No meshes to clean. Run Reconstruct first.")
    hub.loop = asyncio.get_running_loop()
    job, job_dir = hub.job, hub.job_dir

    def run() -> None:
        try:
            pipeline.run_simready(job, job_dir, hub.push_from_thread)
        except Exception:  # noqa: BLE001
            job.log.append(LogLine(t=0, stage="error", text=traceback.format_exc(limit=3)))
            job.save(job_dir / "manifest.json")
            hub.push_from_thread(job)

    hub.task = asyncio.create_task(asyncio.to_thread(run))
    return {"ok": True}


class Rename(BaseModel):
    name: str


@app.put("/api/job/name")
async def rename_job(body: Rename) -> dict:
    """Artist-facing project name. The job folder id stays the same."""
    name = " ".join(body.name.split())
    if not 1 <= len(name) <= 60:
        raise HTTPException(400, "Give the project a name (1–60 characters).")
    hub.job.name = name
    await hub.broadcast(hub.job)
    return {"ok": True, "name": name}


class Measure(BaseModel):
    l: float | None = None
    w: float | None = None
    h: float | None = None


@app.put("/api/assets/{asset_id}/measured")
async def set_measured(asset_id: str, m: Measure) -> dict:
    """Tape size in cm for one box; send nulls to clear it."""
    for a in hub.job.assets:
        if a.id == asset_id:
            vals = [m.l, m.w, m.h]
            if all(v is None for v in vals):
                a.measured = None
            elif any(v is None or not 1 <= v <= 300 for v in vals):
                raise HTTPException(400, "Enter length, width and height in cm (1–300).")
            else:
                a.measured = Dims(l=m.l, w=m.w, h=m.h)
            await hub.broadcast(hub.job)
            return {"ok": True}
    raise HTTPException(404, f"No asset {asset_id}.")


@app.post("/api/stage")
async def stage_run() -> dict:
    if hub.busy:
        raise HTTPException(409, "A run is already in progress.")
    if not any(not a.excluded and a.usd for a in hub.job.assets):
        raise HTTPException(400, "No SimReady assets yet. Run SimReady first.")
    hub.loop = asyncio.get_running_loop()
    job, job_dir = hub.job, hub.job_dir

    def run() -> None:
        try:
            pipeline.run_stage(job, job_dir, hub.push_from_thread)
        except Exception:  # noqa: BLE001
            job.log.append(LogLine(t=0, stage="error", text=traceback.format_exc(limit=3)))
            job.save(job_dir / "manifest.json")
            hub.push_from_thread(job)

    hub.task = asyncio.create_task(asyncio.to_thread(run))
    return {"ok": True}


ISAAC_VENV = Path(os.environ.get("SIMSCRUB_ISAAC_VENV", Path.home() / ".simscrub" / "isaac-venv"))
ISAAC_EXE = ISAAC_VENV / "Scripts" / "isaacsim.exe"
isaac_proc: subprocess.Popen | None = None


@app.get("/api/isaac")
def isaac_status() -> dict:
    """Is Isaac Sim installed in its own venv, and is it already running from here?"""
    py = ISAAC_VENV / "Scripts" / "python.exe"
    version = None
    if py.exists():
        r = subprocess.run([str(py), "-c", "import importlib.metadata as m; print(m.version('isaacsim'))"],
                           capture_output=True, text=True, timeout=30)
        version = r.stdout.strip() or None
    running = isaac_proc is not None and isaac_proc.poll() is None
    if version and ISAAC_EXE.exists():
        state = "running" if running else "ready"
    elif py.exists():
        state = "installing"
    else:
        state = "missing"
    scene = None
    sp = hub.job_dir / "isaac_status.json"
    if running and sp.exists():
        try:
            scene = json.loads(sp.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"state": state, "version": version, "venv": str(ISAAC_VENV), "scene": scene}


@app.post("/api/isaac/launch")
def isaac_launch(play: bool = False) -> dict:
    global isaac_proc
    stage = hub.job_dir / "stage.usda"
    if not stage.exists():
        raise HTTPException(400, "Assemble the stage first.")
    if not ISAAC_EXE.exists():
        raise HTTPException(400, "Isaac Sim isn't installed yet.")
    if isaac_proc is not None and isaac_proc.poll() is None:
        raise HTTPException(409, "Isaac Sim is already open.")
    script = ROOT / "isaac" / "open_stage.py"
    status = hub.job_dir / "isaac_status.json"
    status.unlink(missing_ok=True)
    # D3D12: Isaac Sim's default Vulkan path crashes in rtx.scenedb on 8 GB laptop GPUs.
    cmd = [str(ISAAC_EXE), "isaacsim.exp.full.kit", "--/app/vulkan=false", "--exec", str(script)]
    env = {**os.environ, "OMNI_KIT_ACCEPT_EULA": "YES", "SIMSCRUB_STAGE": str(stage.resolve()),
           "SIMSCRUB_STATUS": str(status.resolve()), "SIMSCRUB_PLAY": "1" if play else "0"}
    log = open(hub.job_dir / "isaac.log", "w", encoding="utf-8")
    isaac_proc = subprocess.Popen(cmd, cwd=str(ISAAC_VENV), env=env, stdout=log, stderr=subprocess.STDOUT,
                                  creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    return {"ok": True, "pid": isaac_proc.pid}


@app.post("/api/stage/settle")
async def stage_settle() -> dict:
    if hub.busy:
        raise HTTPException(409, "A run is already in progress.")
    if not (hub.job_dir / "stage.usda").exists():
        raise HTTPException(400, "Assemble the stage first.")
    hub.loop = asyncio.get_running_loop()
    job, job_dir = hub.job, hub.job_dir

    def run() -> None:
        try:
            pipeline.run_settle(job, job_dir, hub.push_from_thread)
        except Exception:  # noqa: BLE001
            job.log.append(LogLine(t=0, stage="error", text=traceback.format_exc(limit=3)))
            job.save(job_dir / "manifest.json")
            hub.push_from_thread(job)

    hub.task = asyncio.create_task(asyncio.to_thread(run))
    return {"ok": True}


@app.get("/api/videos")
def list_videos() -> list[str]:
    """Videos sitting in the project folder, offered as one-click inputs."""
    return sorted(p.name for p in ROOT.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT)


def _video_info(path: Path) -> dict:
    """Facts an artist can check at a glance: is this the right clip?"""
    import cv2

    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cc = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
    cap.release()
    codec = "".join(chr((cc >> 8 * i) & 0xFF) for i in range(4)).strip("\x00 ") or "?"
    return {
        "name": path.name, "size_mb": round(path.stat().st_size / 1e6, 1),
        "duration_s": round(n / fps, 1) if fps else None, "fps": round(fps, 1) if fps else None,
        "width": w, "height": h, "codec": codec,
        # Chrome/Edge only play HEVC with hardware support; the pipeline (OpenCV) reads it either way.
        "browser_may_not_play": codec.lower() in ("hevc", "hev1", "hvc1", "h265"),
    }


def _poster(path: Path) -> Response:
    import cv2

    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.set(cv2.CAP_PROP_POS_FRAMES, n // 10)   # a frame a little way in, past any fade-in
    ok, img = cap.read()
    cap.release()
    if not ok:
        raise HTTPException(404, "Couldn't read a frame from that video.")
    k = 960 / max(img.shape[:2])
    if k < 1:
        img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Response(buf.tobytes(), media_type="image/jpeg")


def _project_video(name: str) -> Path:
    path = (ROOT / name).resolve()
    if path.parent != ROOT.resolve() or not path.is_file() or path.suffix.lower() not in VIDEO_EXT:
        raise HTTPException(404, "No such video in the project folder.")
    return path


def _job_video() -> Path:
    found = sorted(hub.job_dir.glob("source.*"))
    if not found:
        raise HTTPException(404, "This job has no video yet.")
    return found[0]


@app.get("/api/videos/{name}/file")
def project_video_file(name: str) -> FileResponse:
    return FileResponse(_project_video(name))


@app.get("/api/videos/{name}/info")
def project_video_info(name: str) -> dict:
    return _video_info(_project_video(name))


@app.get("/api/videos/{name}/poster")
def project_video_poster(name: str) -> Response:
    return _poster(_project_video(name))


@app.get("/api/job/video/file")
def job_video_file() -> FileResponse:
    return FileResponse(_job_video())


@app.get("/api/job/video/info")
def job_video_info() -> dict:
    info = _video_info(_job_video())
    info["name"] = hub.job.capture.video or info["name"]
    return info


@app.get("/api/job/video/poster")
def job_video_poster() -> Response:
    return _poster(_job_video())


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
