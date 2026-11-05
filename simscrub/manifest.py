"""Job manifest: the single source of truth for a SimScrub run.

Every stage reads and writes ``jobs/<id>/manifest.json``. The UI never holds
state of its own; it renders whatever the manifest says. That keeps the
pipeline resumable, re-runnable per stage, and scriptable from the CLI.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

STAGE_ORDER = ["capture", "segment", "reconstruct", "simready", "stage"]
StageName = Literal["capture", "segment", "reconstruct", "simready", "stage"]


class Status(str, Enum):
    idle = "idle"            # not started
    working = "working"      # running now (amber)
    done = "done"            # finished cleanly (green)
    attention = "attention"  # finished, but an artist should look (orange)
    failed = "failed"        # could not finish (orange chip, plain language)


class StageState(BaseModel):
    status: Status = Status.idle
    progress: float = 0.0
    message: str = ""


class Dims(BaseModel):
    """Box dimensions in centimetres, as read off a tape measure."""

    l: float
    w: float
    h: float


class Step(BaseModel):
    """One visible step of work on an asset, so the artist sees what the tool did."""

    name: str
    status: Literal["waiting", "working", "done", "skipped", "failed"] = "waiting"
    detail: str = ""


class Recon(BaseModel):
    source: Literal["tripo", "triposr", "proxy"] | None = None
    status: Literal["queued", "working", "ready", "proxy", "failed"] = "queued"
    chip: str | None = None          # plain-language reason, never a stack trace
    mesh: str | None = None          # job-relative path to the GLB exactly as generated
    tris: int | None = None          # triangles in that GLB
    size_mb: float | None = None
    texture_px: int | None = None    # base colour texture resolution
    seconds: float | None = None     # generation time at Tripo
    credits: float | None = None     # credits this mesh cost (0 when cached)
    task_id: str | None = None
    steps: list[Step] = Field(default_factory=list)


class Check(BaseModel):
    rule: str
    label: str
    status: Literal["pass", "fixed", "warn", "fail"]
    detail: str = ""


class Asset(BaseModel):
    id: str                                   # BX-01
    label: str = "amazon_box"
    bbox: tuple[float, float, float, float] | None = None  # normalised x, y, w, h in the hero frame
    polygon: list[tuple[float, float]] | None = None        # normalised outline in the hero frame
    track: int | None = None                                # tracker id across the video
    conf: float | None = None                               # detector confidence of the best view
    best_frame: str | None = None                           # job-relative frame the crop came from
    crop: str | None = None                                 # job-relative RGBA crop sent to Tripo
    seen_in: int = 0                                        # frames the box was tracked in
    excluded: bool = False
    flag: str | None = None                                 # why the pipeline pre-excluded it
    measured: Dims | None = None
    dims: Dims | None = None                  # final dims used (measured or inferred)
    dims_source: Literal["tape", "inferred", "estimated"] | None = None
    thumb: str | None = None
    recon: Recon = Field(default_factory=Recon)
    prep: list[Step] = Field(default_factory=list)   # SimReady steps, shown in the UI
    checks: list[Check] = Field(default_factory=list)
    usd: str | None = None                            # job-relative SimReady .usda
    clean_mesh: str | None = None                     # job-relative GLB of the cleaned mesh, for the viewer
    clean_tris: int | None = None


class Capture(BaseModel):
    video: str | None = None
    frame: str | None = None          # hero frame shown in SEGMENT (URL or job-relative path)
    frames_extracted: int = 0
    duration: float | None = None     # seconds
    resolution: tuple[int, int] | None = None
    measurements: list[Dims] = Field(default_factory=list)


class StageInfo(BaseModel):
    room: Dims | None = None  # centimetres
    scale_source: str | None = None
    usd: str | None = None
    thumb: str | None = None


class LogLine(BaseModel):
    t: float
    stage: str
    text: str


class Job(BaseModel):
    id: str
    name: str = "Home office"
    stages: dict[str, StageState] = Field(
        default_factory=lambda: {s: StageState() for s in STAGE_ORDER}
    )
    capture: Capture = Field(default_factory=Capture)
    assets: list[Asset] = Field(default_factory=list)
    stage: StageInfo = Field(default_factory=StageInfo)
    log: list[LogLine] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Job":
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)  # atomic on the same volume

    def active_assets(self) -> list[Asset]:
        return [a for a in self.assets if not a.excluded]

    def to_json(self) -> dict:
        return json.loads(self.model_dump_json())
