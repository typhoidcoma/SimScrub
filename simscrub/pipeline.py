"""Stage runners shared by the CLI and the server.

Each runner mutates the job, calls ``update`` whenever something visible
changes (the server broadcasts, the CLI prints), and saves the manifest.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable

from simscrub.errors import chip_for
from simscrub.manifest import Job, Recon, Status
from simscrub.stages import capture, reconstruct, segment, simready, tripo

Update = Callable[[Job], None]

_model = None


def get_model():
    """Load YOLOE once per process; loading takes a few seconds."""
    global _model
    if _model is None:
        _model = segment.load_model()
    return _model


def _set(job: Job, stage: str, status: Status, progress: float, message: str | None = None) -> None:
    st = job.stages[stage]
    st.status, st.progress = status, round(progress, 3)
    if message is not None:
        st.message = message


def run_capture(job: Job, job_dir: Path, video: Path, update: Update, fps: float = 8.0) -> list[capture.Frame]:
    _set(job, "capture", Status.working, 0.0, f"Reading {video.name}")
    update(job)
    shutil.rmtree(job_dir / "frames", ignore_errors=True)

    def prog(p: float, msg: str) -> None:
        _set(job, "capture", Status.working, p * 0.98, msg)
        job.capture.frames_extracted = int(msg.split()[0])
        update(job)

    try:
        frames, info = capture.extract_frames(video, job_dir, fps=fps, on_progress=prog)
    except Exception as e:  # noqa: BLE001 - shown to the artist as a chip
        _set(job, "capture", Status.failed, 0.0, chip_for(e))
        update(job)
        raise
    (job_dir / "frames.json").write_text(json.dumps([f.to_dict() for f in frames]))
    job.capture.video = video.name
    job.capture.duration = info["duration"]
    job.capture.resolution = info["resolution"]
    job.capture.frames_extracted = len(frames)
    _set(job, "capture", Status.done, 1.0, f"{len(frames)} frames from {info['duration']:.0f}s of video")
    job.save(job_dir / "manifest.json")
    update(job)
    return frames


def load_frames(job_dir: Path) -> list[capture.Frame]:
    return [capture.Frame(**d) for d in json.loads((job_dir / "frames.json").read_text())]


def run_segment(job: Job, job_dir: Path, video: Path | None, update: Update,
                frames: list[capture.Frame] | None = None, conf: float = 0.12) -> None:
    frames = frames or load_frames(job_dir)
    sharp = capture.drop_blurry(frames)
    _set(job, "segment", Status.working, 0.0, "Loading box finder")
    job.assets = []
    update(job)
    model = get_model()

    _set(job, "segment", Status.working, 0.02, f"Looking for cardboard boxes in {len(sharp)} frames")
    update(job)

    def prog(p: float, msg: str) -> None:
        _set(job, "segment", Status.working, 0.02 + p * 0.93, msg)
        update(job)

    shutil.rmtree(job_dir / "crops", ignore_errors=True)
    assets, hero = segment.find_boxes(sharp, job_dir, model=model, video=video, conf=conf, on_progress=prog)
    job.assets = assets
    if hero is None:
        _set(job, "segment", Status.attention, 1.0, "No cardboard boxes found. Try a slower, closer pass.")
    else:
        job.capture.frame = hero.path
        segment.draw_overlay(job_dir, hero, assets, job_dir / "segment_overlay.jpg")
        kept = sum(1 for a in assets if not a.excluded)
        flagged = len(assets) - kept
        msg = f"{kept} boxes found" + (f" · {flagged} flagged" if flagged else "") + " · tap any to change"
        _set(job, "segment", Status.done, 1.0, msg)
    job.save(job_dir / "manifest.json")
    update(job)


def reconstruct_plan(job: Job, job_dir: Path) -> dict:
    """Cost preview for the RECONSTRUCT tab. Balance lookup is free."""
    p = reconstruct.plan(job.assets, job_dir)
    try:
        p["balance"] = tripo.Tripo().balance()
        p["chip"] = None
    except Exception as e:  # noqa: BLE001 - shown as a chip, not a crash
        p["balance"] = None
        p["chip"] = chip_for(e)
    return p


def run_reconstruct(job: Job, job_dir: Path, update: Update) -> None:
    kept = [a for a in job.assets if not a.excluded and a.crop]
    for a in job.assets:
        a.recon = Recon(source="tripo", status="queued") if a in kept else Recon()
    _set(job, "reconstruct", Status.working, 0.0, f"Generating {len(kept)} meshes")
    update(job)

    def changed(_asset) -> None:
        done = sum(1 for a in kept if a.recon.status in ("ready", "proxy", "failed"))
        _set(job, "reconstruct", Status.working, done / max(1, len(kept)))
        update(job)

    try:
        reconstruct.reconstruct(job.assets, job_dir, changed)
    except Exception as e:  # noqa: BLE001 - e.g. bad key: every box falls back to proxy
        chip = chip_for(e)
        for a in kept:
            if a.recon.status in ("queued", "working"):
                reconstruct._to_proxy(a, job_dir, chip)

    ready = sum(1 for a in kept if a.recon.status == "ready")
    proxies = len(kept) - ready
    msg = f"{ready} meshes ready" + (f" · {proxies} proxy" if proxies else "")
    _set(job, "reconstruct", Status.attention if proxies else Status.done, 1.0, msg)
    job.save(job_dir / "manifest.json")
    update(job)


def run_simready(job: Job, job_dir: Path, update: Update) -> None:
    todo = [a for a in job.assets if not a.excluded and a.recon.mesh]
    for a in job.assets:
        a.prep, a.checks = [], []
    _set(job, "simready", Status.working, 0.0, f"Cleaning {len(todo)} assets")
    update(job)

    for n, a in enumerate(todo):
        _set(job, "simready", Status.working, n / max(1, len(todo)), f"{a.id}: cleaning")

        def changed(_a, n=n) -> None:
            done = sum(s.status in ("done", "skipped", "failed") for s in _a.prep) / max(1, len(_a.prep))
            _set(job, "simready", Status.working, (n + done) / max(1, len(todo)))
            update(job)

        simready.prepare(a, job.assets, job_dir, changed)

    failed = [a for a in todo if any(s.status == "failed" for s in a.prep)]
    flagged = [a for a in todo if any(c.status in ("warn", "fail") for c in a.checks)
               or a.recon.status == "proxy"]
    msg = f"{len(todo) - len(failed)}/{len(todo)} assets SimReady"
    if flagged:
        msg += f" · {len(flagged)} need a look"
    _set(job, "simready", Status.attention if (failed or flagged) else Status.done, 1.0, msg)
    job.save(job_dir / "manifest.json")
    update(job)
