"""Stage 3: turn each box crop into a 3D mesh.

Tripo generates a mesh per kept box. Anything that can't be generated
(no credits, rejected image, timeout) becomes a PROXY: a cuboid with the
box's on-screen proportions, which the SimReady stage later scales to real
size. The pipeline never dead-ends on a bad generation.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import trimesh

from simscrub.errors import NoCredits, chip_for
from simscrub.manifest import Asset, Recon, Step
from simscrub.stages import tripo

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache" / "tripo"

Changed = Callable[[Asset], None]


def cache_key(crop: Path) -> str:
    """Same crop = same mesh, so never pay twice. The settings it was generated
    with are recorded in the meta file and shown on the card, so changing a
    setting never silently re-buys meshes you already have."""
    return hashlib.sha256(crop.read_bytes()).hexdigest()[:24]


def cached(crop: Path) -> Path | None:
    p = CACHE / f"{cache_key(crop)}.glb"
    return p if p.exists() else None


def plan(assets: list[Asset], job_dir: Path) -> dict:
    """What a run would cost, before spending anything (cached and fresh)."""
    todo = [a for a in assets if not a.excluded and a.crop]
    hits = [a for a in todo if cached(job_dir / a.crop)]
    need = len(todo) - len(hits)
    return {
        "boxes": len(todo),
        "cached": len(hits),
        "to_generate": need,
        "credits": need * tripo.CREDITS_PER_MODEL,
        "credits_per_model": tripo.CREDITS_PER_MODEL,
        # "Regenerate": ignore the cache and buy a new mesh for every box.
        "fresh_credits": len(todo) * tripo.CREDITS_PER_MODEL,
    }


def proxy_mesh(crop: Path, dest: Path) -> int:
    """Cuboid with the box's visible proportions; true size is set in SimReady."""
    w, h = 1.0, 0.75
    img = cv2.imread(str(crop), cv2.IMREAD_UNCHANGED)
    if img is not None and img.ndim == 3 and img.shape[2] == 4:
        ys, xs = np.nonzero(img[:, :, 3] > 127)
        if len(xs):
            w, h = float(np.ptp(xs) + 1), float(np.ptp(ys) + 1)
    k = 1.0 / max(w, h)
    box = trimesh.creation.box(extents=(w * k, h * k * 0.8, w * k * 0.7))
    dest.parent.mkdir(parents=True, exist_ok=True)
    box.export(dest)
    return len(box.faces)


def _to_proxy(a: Asset, job_dir: Path, chip: str) -> None:
    rel = f"meshes/{a.id}.glb"
    tris = proxy_mesh(job_dir / a.crop, job_dir / rel)
    steps = [s for s in a.recon.steps if s.status == "done"]
    steps += [
        Step(name="Tripo generation", status="failed", detail=chip),
        Step(name="Fitted proxy cuboid", status="done",
             detail="12 tris · on-screen proportions · true size set in SimReady"),
    ]
    a.recon = Recon(source="proxy", status="proxy", chip=chip, mesh=rel, tris=tris, steps=steps)


def mesh_stats(path: Path) -> dict:
    """Plain facts about a GLB, shown on the card exactly as received."""
    m = trimesh.load(path, force="mesh")
    tex = None
    mat = getattr(m.visual, "material", None)
    img = getattr(mat, "baseColorTexture", None) if mat is not None else None
    if img is not None:
        tex = int(max(img.size))
    return {"tris": int(len(m.faces)), "size_mb": round(path.stat().st_size / 1e6, 1), "texture_px": tex}


def _meta_path(key: str) -> Path:
    return CACHE / f"{key}.json"


def _generate(client: tripo.Tripo, a: Asset, job_dir: Path, changed: Changed, fresh: bool = False) -> None:
    crop = job_dir / a.crop
    rel = f"meshes/{a.id}.glb"
    key = cache_key(crop)
    up = Step(name="Upload photo crop")
    gen = Step(name="Generate textured mesh (Tripo)")
    dl = Step(name="Download mesh")
    a.recon = Recon(source="tripo", status="working", steps=[up, gen, dl])

    def step(st: Step, status: str, detail: str = "") -> None:
        st.status, st.detail = status, detail
        changed(a)

    try:
        hit = None if fresh else cached(crop)
        if hit is not None:
            # Never pay twice: a cached GLB is always reused, stats or not.
            if _meta_path(key).exists():
                meta = json.loads(_meta_path(key).read_text())
            else:
                meta = {"credits": tripo.CREDITS_PER_MODEL, **mesh_stats(hit)}
                _meta_path(key).write_text(json.dumps(meta))
            step(up, "skipped", "Same crop generated before")
            step(gen, "skipped", f"Reused cached mesh · 0 credits (was {meta.get('credits', '?')})")
            step(dl, "skipped", "Loaded from local cache")
            credits = 0.0
        else:
            step(up, "working", f"{crop.name} · {crop.stat().st_size / 1e6:.1f} MB")
            token = client.upload(crop)
            step(up, "done", "Uploaded")
            step(gen, "working", "Submitting")
            task_id = client.image_to_model(token)
            a.recon.task_id = task_id

            def prog(status: str, pct: float) -> None:
                text = {"queued": "Waiting in Tripo's queue", "running": f"Generating · {pct:.0f}%"}.get(status, status)
                step(gen, "working", text)

            d = client.wait(task_id, on_progress=prog)
            credits = float(d.get("credits_consumed") or tripo.CREDITS_PER_MODEL)
            seconds = _duration(d)
            step(gen, "done", f"{credits:.0f} credits" + (f" · {seconds:.0f}s at Tripo" if seconds else ""))
            step(dl, "working", "Downloading GLB")
            CACHE.mkdir(parents=True, exist_ok=True)
            hit = client.download(d["output"]["model_url"], CACHE / f"{key}.glb")
            meta = {"credits": credits, "seconds": seconds, "task_id": task_id,
                    "settings": tripo.SETTINGS, **mesh_stats(hit)}
            _meta_path(key).write_text(json.dumps(meta))
            step(dl, "done", f"{meta['size_mb']} MB")

        (job_dir / "meshes").mkdir(exist_ok=True)
        shutil.copyfile(hit, job_dir / rel)
        if "tris" not in meta:
            meta.update(mesh_stats(job_dir / rel))
        a.recon = Recon(
            source="tripo", status="ready", mesh=rel, tris=meta["tris"], size_mb=meta["size_mb"],
            texture_px=meta.get("texture_px"), seconds=meta.get("seconds"), credits=credits,
            task_id=meta.get("task_id"), steps=a.recon.steps,
        )
    except Exception as e:  # noqa: BLE001 - any failure becomes a proxy with a reason
        _to_proxy(a, job_dir, chip_for(e))
    changed(a)


def _duration(d: dict) -> float | None:
    from datetime import datetime

    try:
        t0 = datetime.fromisoformat(d["created_at"].replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(d["completed_at"].replace("Z", "+00:00"))
        return round((t1 - t0).total_seconds(), 1)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def reconstruct(
    assets: list[Asset],
    job_dir: Path,
    changed: Changed,
    client: tripo.Tripo | None = None,
    workers: int = 3,
    fresh: bool = False,
) -> None:
    """Generate a mesh for every kept box; proxies fill any gaps.

    ``fresh`` ignores the cache and buys a new mesh for every box (the artist
    asked for a regeneration); the new mesh replaces the cached one.
    """
    todo = [a for a in assets if not a.excluded and a.crop]
    for a in todo:
        a.recon = Recon(source="tripo", status="queued")
    for a in assets:
        if a.excluded:
            a.recon = Recon()

    need = [a for a in todo if fresh or not cached(job_dir / a.crop)]
    if need:
        client = client or tripo.Tripo()
        n = int(client.balance() // tripo.CREDITS_PER_MODEL)
        if n < len(need):
            # Spend what we have on the clearest boxes; the rest become proxies.
            need.sort(key=lambda a: a.conf or 0, reverse=True)
            for a in need[n:]:
                _to_proxy(a, job_dir, NoCredits.chip)
                changed(a)

    run = [a for a in todo if a.recon.status == "queued"]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda a: _generate(client, a, job_dir, changed, fresh), run))
