"""Stage 4: clean each raw mesh into a SimReady USD asset, one visible step at a time.

    1 Repair         floaters, duplicate verts, degenerate faces   (MeshLab, isolated)
    2 Decimate       ~1.4M -> ~20k tris, texture kept               (MeshLab, isolated)
    3 Orient         Z-up, sides aligned to X/Y, base on z = 0
    4 Scale          true size from the tape measurements, in meters
    5 Collider       convex hull for PhysX
    6 Physics+label  rigid body, cardboard mass and friction, class label
    7 Write USD      asset .usda + texture, plus a GLB for the viewer
    8 Validate       rule registry run against the written file

Every step writes its result into the asset's step list, so the artist sees
exactly what changed and by how much.
"""

from __future__ import annotations

import math
import shutil
import traceback
from pathlib import Path
from typing import Callable

import numpy as np
import trimesh

from simscrub.errors import chip_for
from simscrub.manifest import Asset, Check, Dims, Step
from simscrub.stages import meshops
from simscrub.usd.author import CARDBOARD_RGB, CARDBOARD_ROUGHNESS, FRICTION, LABEL, BoxAsset, write_box
from simscrub.usd.validate import validate

Changed = Callable[[Asset], None]

TARGET_TRIS = 20_000
CARDBOARD_GSM = 550           # g/m², single-wall corrugated
DEFAULT_LONG_SIDE_M = 0.40    # only when no box was tape-measured at all

STEP_NAMES = ["Repair", "Decimate", "Orient", "Scale to tape size", "Collider",
              "Physics + label", "Write USD", "Validate"]

Y_UP_TO_Z_UP = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], float)
Z_UP_TO_Y_UP = np.linalg.inv(Y_UP_TO_Z_UP)


def _fmt(n: int) -> str:
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n:,}"


def _load(path: Path) -> trimesh.Trimesh:
    return trimesh.load(path, force="mesh", process=False)


def _orient(mesh: trimesh.Trimesh) -> str:
    """Z-up, sides on X/Y (longest along X), base on z=0. Returns what it did."""
    mesh.apply_transform(Y_UP_TO_Z_UP)  # glTF is Y-up; USD stage is Z-up
    to_origin, ext = trimesh.bounds.oriented_bounds(mesh)
    R = to_origin[:3, :3]                                # rows = box axes in world
    up = int(np.argmax(np.abs(R[:, 2])))
    tilt = math.degrees(math.acos(min(1.0, abs(R[up, 2]))))

    if tilt <= 25:
        # Trust the box: level it on its own base.
        z = R[up] * np.sign(R[up, 2])
        side = [i for i in range(3) if i != up]
        side.sort(key=lambda i: -ext[i])
        x = R[side[0]] - z * (R[side[0]] @ z)
        x /= np.linalg.norm(x)
        levelled = f"levelled {tilt:.1f}° tilt"
    else:
        # Box isn't clearly resting on a face; keep Tripo's up, only fix the yaw.
        z = np.array([0.0, 0.0, 1.0])
        flat = mesh.vertices[:, :2]
        _, (w, h), ang = __import__("cv2").minAreaRect(flat.astype(np.float32))
        a = math.radians(ang if w >= h else ang + 90)
        x = np.array([math.cos(a), math.sin(a), 0.0])
        levelled = f"kept Tripo's up (best face {tilt:.0f}° off)"
    y = np.cross(z, x)
    rot = np.eye(4)
    rot[:3, :3] = np.vstack([x, y, z])
    mesh.apply_transform(rot)
    yaw = math.degrees(math.atan2(x[1], x[0]))

    lo, hi = mesh.bounds
    shift = np.eye(4)
    shift[:3, 3] = [-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]]
    mesh.apply_transform(shift)
    return f"Z-up · {levelled} · yawed {yaw:+.0f}° · base on z=0"


def _target_dims(a: Asset, all_assets: list[Asset], cur: np.ndarray) -> tuple[np.ndarray, str, str]:
    """Target size in meters (x=long, y=short, z=height), source, and a sentence."""
    if a.measured:
        lw = sorted([a.measured.l, a.measured.w], reverse=True)
        t = np.array([lw[0], lw[1], a.measured.h]) / 100
        return t, "tape", "tape measurement"
    longs = [max(b.measured.l, b.measured.w) for b in all_assets if b.measured and not b.excluded]
    if longs:
        long_m = float(np.median(longs)) / 100
        why = f"no tape size; matched median measured box ({long_m * 100:.0f} cm long)"
        src = "inferred"
    else:
        long_m = DEFAULT_LONG_SIDE_M
        why = f"no tape sizes entered; assumed {long_m * 100:.0f} cm long"
        src = "estimated"
    return cur * (long_m / cur[0]), src, why


def prepare(a: Asset, assets: list[Asset], job_dir: Path, changed: Changed) -> None:
    steps = {n: Step(name=n) for n in STEP_NAMES}
    a.prep = list(steps.values())
    a.checks = []
    changed(a)

    def step(name: str, status: str, detail: str = "") -> None:
        steps[name].status, steps[name].detail = status, detail
        changed(a)

    work = job_dir / "work" / a.id
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    out_dir = job_dir / "assets" / a.id
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    raw = job_dir / a.recon.mesh
    proxy = a.recon.status == "proxy"
    current = "Repair"

    try:
        # ---- 1 + 2: heavy geometry work, isolated ----
        if proxy:
            step("Repair", "skipped", "Proxy cuboid is already clean (12 tris)")
            step("Decimate", "skipped", "Nothing to reduce")
            mesh = _load(raw)
        else:
            step("Repair", "working", f"Reading {_fmt(a.recon.tris or 0)}-tri mesh")
            _load(raw).export(work / "raw.obj")
            step("Repair", "working", "Removing floaters and duplicate geometry")
            r = meshops.run_isolated("repair", work / "raw.obj", work / "repaired.obj")
            if r["floaters_removed"]:
                n = r["floaters_removed"]
                bits = [f"{n} floating piece{'s' if n != 1 else ''} removed ({_fmt(r['floater_faces'])} tris)"]
            else:
                bits = ["No floating pieces"]
            if r["duplicate_verts"]:
                bits.append(f"{_fmt(r['duplicate_verts'])} duplicate verts merged"
                            + (f" (re-joined {r['stitched_pieces']} split pieces)" if r["stitched_pieces"] else ""))
            if r["degenerate_faces"]:
                bits.append(f"{_fmt(r['degenerate_faces'])} degenerate faces dropped")
            step("Repair", "done", " · ".join(bits))

            current = "Decimate"
            step("Decimate", "working", f"{_fmt(r['after']['faces'])} → ~{_fmt(TARGET_TRIS)} tris, keeping UVs")
            d = meshops.run_isolated("decimate", work / "repaired.obj", work / "decimated.obj", TARGET_TRIS)
            pct = 100 * (1 - d["after"] / max(1, d["before"]))
            step("Decimate", "done", f"{_fmt(d['before'])} → {_fmt(d['after'])} tris (−{pct:.1f}%)")
            mesh = _load(work / "decimated.obj")

        # ---- 3: orient ----
        current = "Orient"
        step("Orient", "working")
        step("Orient", "done", _orient(mesh))

        # ---- 4: scale ----
        current = "Scale to tape size"
        cur = mesh.extents.copy()
        target, src, why = _target_dims(a, assets, cur)
        s = target / cur
        S = np.diag([*s, 1.0])
        mesh.apply_transform(S)
        a.dims = Dims(l=round(target[0] * 100, 1), w=round(target[1] * 100, 1), h=round(target[2] * 100, 1))
        a.dims_source = src
        before = " × ".join(f"{v:.2f}" for v in cur)
        after = " × ".join(f"{v * 100:.1f}" for v in target)
        step("Scale to tape size", "done" if src == "tape" else "working",
             f"{before} units → {after} cm · {why}")
        if src != "tape":
            steps["Scale to tape size"].status = "done"
            steps["Scale to tape size"].detail += " · enter a tape size for true scale"
            changed(a)

        # ---- 5: collider ----
        current = "Collider"
        hull = mesh.convex_hull
        step("Collider", "done",
             f"Convex hull · {len(hull.faces)} tris · {hull.volume * 1000:.1f} L · PhysX cooks the same hull")

        # ---- 6: physics + label ----
        current = "Physics + label"
        L, W, H = target
        area = 2 * (L * W + L * H + W * H)
        mass = area * CARDBOARD_GSM / 1000
        step("Physics + label", "done",
             f"Rigid body · {mass:.2f} kg ({area:.2f} m² × {CARDBOARD_GSM} g/m²) · "
             f"friction {FRICTION[0]}/{FRICTION[1]} · class '{LABEL}'")

        # ---- 7: write USD (+ viewer GLB) ----
        current = "Write USD"
        step("Write USD", "working")
        texture = None
        img = getattr(getattr(mesh.visual, "material", None), "image", None)
        if img is None:
            img = getattr(getattr(mesh.visual, "material", None), "baseColorTexture", None)
        uv = getattr(mesh.visual, "uv", None)
        if img is not None and uv is not None and not proxy:
            texture = f"{a.id}_albedo.png"
            img.convert("RGB").save(out_dir / texture)
        usd = out_dir / f"{a.id}.usda"
        write_box(usd, BoxAsset(
            name=a.id.replace("-", "_"), vertices=np.asarray(mesh.vertices), faces=np.asarray(mesh.faces),
            uv=np.asarray(uv) if texture else None, texture=texture, mass_kg=mass,
        ))
        view = mesh.copy()
        view.apply_transform(Z_UP_TO_Y_UP)
        # The viewer must show what the USD will render. OBJ round-trips drop
        # metallic, and glTF then defaults it to 1.0 (metal), which renders the
        # cardboard dark. Write the same values the USD material uses.
        view.visual.material = trimesh.visual.material.PBRMaterial(
            baseColorTexture=img if texture else None,
            baseColorFactor=None if texture else [*CARDBOARD_RGB, 1.0],
            metallicFactor=0.0,
            roughnessFactor=CARDBOARD_ROUGHNESS,
            doubleSided=True,
        )
        view.export(out_dir / f"{a.id}_view.glb")
        a.usd = f"assets/{a.id}/{usd.name}"
        a.clean_mesh = f"assets/{a.id}/{a.id}_view.glb"
        a.clean_tris = int(len(mesh.faces))
        size = sum(p.stat().st_size for p in out_dir.iterdir() if p.suffix in (".usda", ".png")) / 1e6
        step("Write USD", "done", f"{usd.name} · {_fmt(len(mesh.faces))} tris · "
                                  f"{'texture ' + str(img.size[0]) + 'px' if texture else 'flat cardboard colour'} · "
                                  f"{size:.1f} MB")

        # ---- 8: validate the file on disk ----
        current = "Validate"
        step("Validate", "working", "Opening the written file")
        a.checks = validate(usd)
        # The file can't know where its size came from; the pipeline does.
        a.checks.append(Check(
            rule="scale_source", label="Tape-measured size",
            status="pass" if a.dims_source == "tape" else "warn",
            detail="From tape measurement" if a.dims_source == "tape"
            else ("Matched to measured boxes" if a.dims_source == "inferred" else "Estimated · enter L×W×H"),
        ))
        n_pass = sum(c.status == "pass" for c in a.checks)
        bad = [c for c in a.checks if c.status in ("warn", "fail")]
        step("Validate", "failed" if any(c.status == "fail" for c in bad) else "done",
             f"{n_pass}/{len(a.checks)} checks pass" + (f" · {len(bad)} need a look" if bad else ""))
    except Exception as e:  # noqa: BLE001 - show which step failed, in plain words
        traceback.print_exc()  # full detail for the developer log; the artist sees the chip
        step(current, "failed", chip_for(e))
        for n in STEP_NAMES[STEP_NAMES.index(current) + 1:]:
            steps[n].status = "skipped"
        changed(a)
