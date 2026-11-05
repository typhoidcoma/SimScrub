"""Command line entry point. Every stage the UI runs can be run from here."""

from __future__ import annotations

import time
from pathlib import Path

import typer

from simscrub import pipeline
from simscrub.manifest import Job

app = typer.Typer(add_completion=False, help="TE SimScrub: video in, SimReady USD out.")
ROOT = Path(__file__).resolve().parent.parent


@app.callback()
def main() -> None:
    """Run pipeline stages on a job folder."""


def _job(name: str) -> tuple[Job, Path]:
    job_dir = ROOT / "jobs" / name
    path = job_dir / "manifest.json"
    job = Job.load(path) if path.exists() else Job(id=name, name=name)
    return job, job_dir


def _printer():
    last: dict[str, float] = {}

    def update(job: Job) -> None:
        for name, st in job.stages.items():
            if st.status.value != "working":
                continue
            if st.progress - last.get(name, -1) >= 0.1:
                last[name] = st.progress
                typer.echo(f"  {name:<8} {st.progress * 100:5.1f}%  {st.message}")
    return update


@app.command("segment")
def segment_cmd(
    video: Path = typer.Argument(..., exists=True, dir_okay=False, help="Walkthrough video"),
    job: str = typer.Option("office", help="Job folder name under jobs/"),
    fps: float = typer.Option(8.0, help="Frames sampled per second of video"),
    conf: float = typer.Option(0.12, help="Detector confidence threshold"),
    keep_frames: bool = typer.Option(False, help="Reuse frames already extracted for this job"),
):
    """Extract frames, find every cardboard box, save a crop per box."""
    j, job_dir = _job(job)
    t0 = time.time()
    update = _printer()
    frames = None
    if keep_frames and (job_dir / "frames.json").exists():
        typer.echo("Reusing extracted frames")
    else:
        frames = pipeline.run_capture(j, job_dir, video, update, fps=fps)
    pipeline.run_segment(j, job_dir, video, update, frames=frames, conf=conf)

    typer.echo(f"\n{j.stages['segment'].message}  ({time.time() - t0:.0f}s)")
    typer.echo(f"Hero frame: {j.capture.frame}")
    for a in j.assets:
        typer.echo(f"  {a.id}  seen in {a.seen_in:>3} frames  conf {a.conf:.2f}  "
                   f"{'in hero' if a.polygon else '       '}  {a.flag or 'kept'}")
    typer.echo(f"\nOverlay: {job_dir / 'segment_overlay.jpg'}")


if __name__ == "__main__":
    app()
