"""Stage 2: find every cardboard box in the walkthrough.

YOLOE-seg takes a text prompt ("cardboard box") and returns an outline per
box in each frame. A tracker links detections across frames so each physical
box becomes one asset, and the clearest view of each box becomes its crop for
Tripo. The frame showing the most boxes becomes the hero frame for the UI.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from simscrub.manifest import Asset
from simscrub.stages.capture import Frame

Progress = Callable[[float, str], None]

PROMPTS = ["cardboard box", "brown cardboard box"]
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WEIGHTS = ROOT / "models" / "yoloe-26l-seg.pt"


@dataclass
class Obs:
    frame: Frame
    xyxy: np.ndarray        # pixel box in the frame
    polygon: np.ndarray     # normalised outline, (N, 2)
    conf: float
    area: float             # fraction of the frame the mask covers
    edge: bool              # box touches the frame border (probably cut off)

    def score(self, sharp_ref: float) -> float:
        sharp = min(1.5, self.frame.sharpness / sharp_ref)
        return self.area ** 0.5 * self.conf * sharp * (0.35 if self.edge else 1.0)


@dataclass
class Track:
    id: int
    obs: list[Obs] = field(default_factory=list)
    last_n: int = -1                 # index of the last frame this box was seen in
    last_box: np.ndarray | None = None


def load_model(weights: Path = DEFAULT_WEIGHTS, prompts: list[str] = PROMPTS):
    from ultralytics import YOLOE

    model = YOLOE(str(weights))
    model.set_classes(prompts, model.get_text_pe(prompts))
    return model


def iou(a: np.ndarray, b: np.ndarray) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def camera_motion(prev: np.ndarray, curr: np.ndarray) -> np.ndarray | None:
    """Affine transform (2x3) of the whole frame from prev to curr.

    The scene is static and the camera moves, so background features tell us
    where every box from the last frame should be now.
    """
    pts = cv2.goodFeaturesToTrack(prev, maxCorners=600, qualityLevel=0.01, minDistance=8)
    if pts is None or len(pts) < 12:
        return None
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, curr, pts, None, winSize=(31, 31), maxLevel=4)
    ok = st.ravel() == 1
    if ok.sum() < 12:
        return None
    M, _ = cv2.estimateAffinePartial2D(pts[ok], nxt[ok], method=cv2.RANSAC, ransacReprojThreshold=4.0)
    return M


def warp_box(box: np.ndarray, M: np.ndarray) -> np.ndarray:
    x0, y0, x1, y1 = box
    c = np.array([[x0, y0], [x1, y0], [x0, y1], [x1, y1]], np.float32)
    c = c @ M[:, :2].T + M[:, 2]
    return np.array([c[:, 0].min(), c[:, 1].min(), c[:, 0].max(), c[:, 1].max()])


def detect(model, img: np.ndarray, conf: float) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """One frame: (pixel xyxy, normalised outline, confidence) per box."""
    res = model.predict(img, conf=conf, agnostic_nms=True, verbose=False)[0]
    if res.boxes is None or res.masks is None:
        return []
    out = []
    for box, poly, c in zip(res.boxes.xyxy.cpu().numpy(), res.masks.xyn, res.boxes.conf.tolist()):
        if len(poly) >= 3:
            out.append((box, poly, float(c)))
    return out


def track_boxes(
    model,
    frames: list[Frame],
    job_dir: Path,
    conf: float = 0.12,
    min_iou: float = 0.25,
    max_gap: int = 48,
    on_progress: Progress | None = None,
) -> dict[int, Track]:
    """Link per-frame detections into one track per physical box.

    Open-vocabulary detections flicker (a box scores 0.2, drops out, comes
    back), so every detection is kept and linked greedily to the track it
    overlaps most, as long as that track was seen within ``max_gap`` frames.
    """
    tracks: dict[int, Track] = {}
    next_id = 1
    prev_gray = None
    for n, f in enumerate(frames):
        img = cv2.imread(str(job_dir / f.path))
        h, w = img.shape[:2]
        dets = detect(model, img, conf)

        # Move every remembered box along with the camera before matching.
        gray = cv2.cvtColor(cv2.resize(img, None, fx=0.5, fy=0.5), cv2.COLOR_BGR2GRAY)
        if prev_gray is not None:
            M = camera_motion(prev_gray, gray)
            if M is not None:
                M[:, 2] *= 2  # motion was measured at half scale
                for t in tracks.values():
                    if n - t.last_n <= max_gap:
                        t.last_box = warp_box(t.last_box, M)
        prev_gray = gray

        live = [t for t in tracks.values() if n - t.last_n <= max_gap]
        pairs = sorted(
            ((iou(t.last_box, d[0]), ti, di) for ti, t in enumerate(live) for di, d in enumerate(dets)),
            reverse=True,
        )
        used_t, match = set(), {}
        for score, ti, di in pairs:
            if score < min_iou:
                break
            if ti in used_t or di in match:
                continue
            used_t.add(ti)
            match[di] = live[ti]

        for di, (box, poly, c) in enumerate(dets):
            t = match.get(di)
            if t is None:
                t = tracks[next_id] = Track(next_id)
                next_id += 1
            area = float(cv2.contourArea((poly * [w, h]).astype(np.float32))) / (w * h)
            m = 4  # px from the border counts as cut off
            edge = bool(box[0] < m or box[1] < m or box[2] > w - m or box[3] > h - m)
            t.obs.append(Obs(frame=f, xyxy=box, polygon=poly, conf=c, area=area, edge=edge))
            t.last_n, t.last_box = n, box

        if on_progress:
            on_progress((n + 1) / len(frames), f"{len(tracks)} candidate boxes")
    return tracks


def crop_box(img: np.ndarray, obs: Obs, pad: float = 0.08) -> np.ndarray:
    """Cut out one box: masked to its outline, transparent background, padded."""
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [(obs.polygon * [w, h]).astype(np.int32)], 255)
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    x0, y0, x1, y1 = obs.xyxy
    px, py = (x1 - x0) * pad, (y1 - y0) * pad
    x0, y0 = int(max(0, x0 - px)), int(max(0, y0 - py))
    x1, y1 = int(min(w, x1 + px)), int(min(h, y1 + py))
    rgba = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    rgba[:, :, 3] = mask
    return rgba[y0:y1, x0:x1]


def read_source_frame(video: Path, index: int) -> np.ndarray | None:
    """Full-resolution frame straight from the video, for the sharpest crops."""
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, img = cap.read()
    cap.release()
    return img if ok else None


def find_boxes(
    frames: list[Frame],
    job_dir: Path,
    model=None,
    video: Path | None = None,
    conf: float = 0.12,
    min_frames: int = 3,
    weak_conf: float = 0.32,
    weak_frames: int = 10,
    on_progress: Progress | None = None,
) -> tuple[list[Asset], Frame | None]:
    """Detect and track boxes, save a crop per box, and pick the hero frame."""
    model = model or load_model()
    tracks = track_boxes(model, frames, job_dir, conf=conf, on_progress=on_progress)

    # A real box shows up in several frames; one-frame blips are noise.
    tracks = {k: t for k, t in tracks.items() if len(t.obs) >= min_frames}
    if not tracks:
        return [], None

    sharp_ref = float(np.median([f.sharpness for f in frames])) or 1.0

    # Hero frame: the one that shows the most boxes, then the most box area.
    per_frame: dict[str, list[tuple[int, Obs]]] = defaultdict(list)
    for t in tracks.values():
        for o in t.obs:
            per_frame[o.frame.path].append((t.id, o))
    hero_path = max(
        per_frame,
        key=lambda p: (len(per_frame[p]), sum(o.area for _, o in per_frame[p]) * per_frame[p][0][1].frame.sharpness),
    )
    hero = per_frame[hero_path][0][1].frame
    in_hero = {tid: o for tid, o in per_frame[hero_path]}

    # Order boxes left to right as they appear in the hero frame, the rest after.
    def order(t: Track) -> tuple:
        o = in_hero.get(t.id)
        return (0, float(o.xyxy[0])) if o else (1, t.obs[0].frame.t)

    crops = job_dir / "crops"
    crops.mkdir(parents=True, exist_ok=True)
    assets: list[Asset] = []
    for n, t in enumerate(sorted(tracks.values(), key=order), 1):
        aid = f"BX-{n:02d}"
        best = max(t.obs, key=lambda o: o.score(sharp_ref))
        img = cv2.imread(str(job_dir / best.frame.path))
        src = read_source_frame(video, best.frame.index) if video else None
        rel = f"crops/{aid}.png"
        if src is not None:
            # Detection ran on a scaled frame; xyxy is in those pixels.
            k = src.shape[1] / img.shape[1]
            full = Obs(best.frame, best.xyxy * k, best.polygon, best.conf, best.area, best.edge)
            cv2.imwrite(str(job_dir / rel), crop_box(src, full))
        else:
            cv2.imwrite(str(job_dir / rel), crop_box(img, best))

        a = Asset(
            id=aid, track=t.id, conf=round(best.conf, 3), best_frame=best.frame.path,
            crop=rel, thumb=rel, seen_in=len(t.obs),
        )
        if (o := in_hero.get(t.id)) is not None:
            hh, ww = img.shape[:2]
            x0, y0, x1, y1 = o.xyxy
            a.bbox = (x0 / ww, y0 / hh, (x1 - x0) / ww, (y1 - y0) / hh)
            step = max(1, len(o.polygon) // 80)  # keep the outline light for the UI
            a.polygon = [(round(float(x), 4), round(float(y), 4)) for x, y in o.polygon[::step]]
        # Likely junk (a flap, a fragment, a non-box): pre-exclude, but let
        # the artist tap it back in SEGMENT if the call was wrong.
        if a.conf < weak_conf and a.seen_in < weak_frames:
            a.excluded = True
            a.flag = f"Low confidence ({a.conf:.2f}, {a.seen_in} frames). Tap to keep."
        assets.append(a)
    return assets, hero


def draw_overlay(job_dir: Path, hero: Frame, assets: list[Asset], out: Path) -> None:
    """Debug image: hero frame with numbered outlines, for checking from the CLI."""
    img = cv2.imread(str(job_dir / hero.path))
    h, w = img.shape[:2]
    orange = (40, 90, 240)
    for a in assets:
        if not a.polygon:
            continue
        pts = (np.array(a.polygon) * [w, h]).astype(np.int32)
        cv2.polylines(img, [pts], True, orange, 3)
        x, y = pts.min(axis=0)
        cv2.rectangle(img, (int(x), int(y) - 26), (int(x) + 78, int(y)), orange, -1)
        cv2.putText(img, a.id, (int(x) + 5, int(y) - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.imwrite(str(out), img)
