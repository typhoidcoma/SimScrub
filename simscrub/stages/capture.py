"""Stage 1: pull frames out of the walkthrough video.

Samples the video at a fixed rate, scales frames down for detection, and
scores each frame's sharpness so blurry frames (motion blur from walking)
can be skipped later.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from simscrub.errors import VideoUnreadable

Progress = Callable[[float, str], None]


@dataclass
class Frame:
    index: int        # frame number in the source video
    t: float          # seconds
    path: str         # job-relative path
    sharpness: float  # variance of the Laplacian; higher is sharper

    def to_dict(self) -> dict:
        return asdict(self)


def sharpness(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def extract_frames(
    video: Path,
    job_dir: Path,
    fps: float = 4.0,
    max_side: int = 1600,
    on_progress: Progress | None = None,
) -> tuple[list[Frame], dict]:
    """Write sampled frames to ``job_dir/frames`` and return them with video info."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise VideoUnreadable(f"OpenCV could not open {video}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    step = max(1, round(src_fps / fps))
    out_dir = job_dir / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)

    frames: list[Frame] = []
    size: tuple[int, int] | None = None
    i = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, img = cap.retrieve()
            if not ok:
                break
            h, w = img.shape[:2]
            k = max_side / max(h, w)
            if k < 1:
                img = cv2.resize(img, (round(w * k), round(h * k)), interpolation=cv2.INTER_AREA)
            size = (img.shape[1], img.shape[0])
            rel = f"frames/f{i:06d}.jpg"
            cv2.imwrite(str(job_dir / rel), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            gray = cv2.cvtColor(cv2.resize(img, None, fx=0.5, fy=0.5), cv2.COLOR_BGR2GRAY)
            frames.append(Frame(index=i, t=round(i / src_fps, 3), path=rel, sharpness=sharpness(gray)))
            if on_progress and total:
                on_progress(min(1.0, i / total), f"{len(frames)} frames")
        i += 1
    cap.release()

    if not frames:
        raise VideoUnreadable(f"No frames decoded from {video}")
    info = {"duration": round(i / src_fps, 2), "resolution": size, "src_fps": src_fps}
    return frames, info


def drop_blurry(frames: list[Frame], keep_ratio: float = 0.4) -> list[Frame]:
    """Drop frames much blurrier than the video's median."""
    med = float(np.median([f.sharpness for f in frames]))
    return [f for f in frames if f.sharpness >= keep_ratio * med]
