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


@app.command("reconstruct")
def reconstruct_cmd(
    job: str = typer.Option("office", help="Job folder name under jobs/"),
    plan_only: bool = typer.Option(False, "--plan", help="Show the cost and balance, spend nothing"),
):
    """Generate a mesh per kept box with Tripo; proxies fill any gaps."""
    j, job_dir = _job(job)
    p = pipeline.reconstruct_plan(j, job_dir)
    bal = "unknown" if p["balance"] is None else f"{p['balance']:.0f}"
    typer.echo(f"{p['boxes']} boxes · {p['cached']} cached · {p['to_generate']} to generate · "
               f"{p['credits']} credits (${p['credits'] / 100:.2f}) · balance {bal}")
    if p["chip"]:
        typer.echo(f"  ! {p['chip']}")
    if plan_only:
        return
    pipeline.run_reconstruct(j, job_dir, _printer())
    typer.echo(f"\n{j.stages['reconstruct'].message}")
    for a in j.assets:
        if a.excluded:
            continue
        r = a.recon
        typer.echo(f"  {a.id}  {r.status:<6} {r.source or '':<6} {r.tris or 0:>8} tris  {r.chip or ''}")


@app.command("simready")
def simready_cmd(job: str = typer.Option("office", help="Job folder name under jobs/")):
    """Clean every mesh into a validated SimReady USD asset, step by step."""
    j, job_dir = _job(job)

    def update(job: Job) -> None:
        pass

    pipeline.run_simready(j, job_dir, update)
    typer.echo(j.stages["simready"].message)
    for a in j.assets:
        if a.excluded or not a.prep:
            continue
        typer.echo("")
        typer.echo(f"{a.id}  {a.usd or ''}")
        for s in a.prep:
            typer.echo(f"  [{s.status:<7}] {s.name:<20} {s.detail}")
        for c in a.checks:
            typer.echo(f"      {c.status:<4} {c.label:<20} {c.detail}")


@app.command("assemble")
def assemble_cmd(job: str = typer.Option("office", help="Job folder name under jobs/")):
    """Assemble all SimReady assets into one physics-ready stage.usda."""
    j, job_dir = _job(job)
    pipeline.run_stage(j, job_dir, lambda _j: None)
    typer.echo(j.stages["stage"].message)
    for s in j.stage.steps:
        typer.echo(f"  [{s.status:<7}] {s.name:<18} {s.detail}")
    for c in j.stage.checks:
        typer.echo(f"      {c.status:<4} {c.label:<26} {c.detail}")
    for b in j.stage.layout:
        typer.echo(f"  {b['id']}  x={b['x']:+.2f} y={b['y']:+.2f} z={b['z']:.2f}  {b['on'] and 'on ' + b['on'] or ''}  {b['source']}")


@app.command("validate")
def validate_cmd(path: Path = typer.Argument(..., exists=True, help="Asset .usda/.usd")):
    """Run the SimReady rule set against any USD asset."""
    from simscrub.usd.validate import validate

    for c in validate(path):
        typer.echo(f"  {c.status:<4} {c.label:<20} {c.detail}")


if __name__ == "__main__":
    app()
