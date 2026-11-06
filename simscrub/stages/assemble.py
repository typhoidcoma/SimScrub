"""Stage 5: assemble every SimReady asset into one physics-ready USD stage.

    1 Place boxes      estimate each box's floor position from the hero frame
    2 Stack            a box sitting on another in the frame goes on top of it
    3 Resolve overlaps push apart boxes whose footprints collide
    4 Floor + physics  ground collider, PhysicsScene (gravity), lights
    5 Write stage.usda references to each asset's USD, never copies
    6 Validate stage   references resolve, nothing below the floor or overlapping
    7 Preview          one GLB of the whole scene for the in-app 3D view

Placement is an estimate, said plainly: one video frame has no depth. The
phone is assumed to be held at 1.3 m, tilted 20° down, ~70° horizontal FOV.
Boxes start a couple of centimetres above their support and settle under
gravity when the simulation starts.
"""

from __future__ import annotations

import math
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import trimesh
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade

from simscrub.errors import chip_for
from simscrub.manifest import Asset, Check, Job, Step
from simscrub.usd.author import asset_name

Changed = Callable[[], None]

CAM_HEIGHT_M = 1.3
CAM_PITCH_DEG = 20.0
CAM_HFOV_DEG = 70.0
DROP_M = 0.02          # spawn gap above the support
GAP_M = 0.05           # minimum gap when pushing overlapping boxes apart
FLOOR_MARGIN_M = 0.6
FLOOR_FRICTION = (0.6, 0.5)

STEP_NAMES = ["Place boxes", "Stack", "Resolve overlaps", "Floor + physics", "Write stage.usda",
              "Validate stage", "Preview"]


@dataclass
class Placed:
    asset: Asset
    x: float
    y: float
    z: float           # base height (0 = floor)
    L: float           # meters, along x
    W: float           # along y
    H: float           # along z
    on: str | None = None   # id of the box it rests on
    source: str = ""        # how the position was chosen

    def footprint(self) -> tuple[float, float, float, float]:
        return self.x - self.L / 2, self.y - self.W / 2, self.x + self.L / 2, self.y + self.W / 2


def _ground_point(u: float, v: float, w: int, h: int) -> tuple[float, float] | None:
    """Pixel -> point on the floor plane, for the assumed phone pose. None if above the horizon."""
    f = (w / 2) / math.tan(math.radians(CAM_HFOV_DEG / 2))
    xc, yc = (u - w / 2) / f, (v - h / 2) / f
    t = math.radians(CAM_PITCH_DEG)
    right = np.array([1.0, 0.0, 0.0])
    fwd = np.array([0.0, math.cos(t), -math.sin(t)])
    down = np.array([0.0, -math.sin(t), -math.cos(t)])
    d = xc * right + yc * down + fwd
    if d[2] >= -1e-6:
        return None
    s = CAM_HEIGHT_M / -d[2]
    return float(s * d[0]), float(s * d[1])


def _place(job: Job, assets: list[Asset]) -> tuple[list[Placed], str]:
    w, h = job.capture.resolution or (1600, 900)
    placed: list[Placed] = []
    off_frame = []
    for a in assets:
        L, W, H = a.dims.l / 100, a.dims.w / 100, a.dims.h / 100
        if a.bbox:
            bx, by, bw, bh = a.bbox
            g = _ground_point((bx + bw / 2) * w, (by + bh) * h, w, h)
            if g is None:
                g, src = (0.0, 4.0), "above horizon → 4 m back"
            else:
                src = "projected from hero frame"
            x, y = g
            y = float(np.clip(y, 0.6, 4.5)) + W / 2   # bottom edge is the near face
            placed.append(Placed(a, x, y, 0.0, L, W, H, source=src))
        else:
            off_frame.append((a, L, W, H))
    # Boxes not in the hero frame: a tidy row behind the others.
    back = max([p.y + p.W / 2 for p in placed], default=1.5) + 0.6
    x = 0.0
    for a, L, W, H in off_frame:
        placed.append(Placed(a, x + L / 2, back + W / 2, 0.0, L, W, H, source="not in hero frame → back row"))
        x += L + 0.3
    n_proj = sum(1 for p in placed if p.source.startswith("projected"))
    return placed, f"{n_proj} from the hero frame" + (f" · {len(off_frame)} in a back row" if off_frame else "")


def _stack(placed: list[Placed]) -> str:
    by_id = {p.asset.id: p for p in placed}
    stacked = []
    for p in placed:
        if not p.asset.bbox:
            continue
        ax, ay, aw, ah = p.asset.bbox
        for q in placed:
            if q is p or not q.asset.bbox or q.on == p.asset.id:
                continue
            bx, by, bw, bh = q.asset.bbox
            overlap = max(0.0, min(ax + aw, bx + bw) - max(ax, bx)) / min(aw, bw)
            a_bottom, b_top = ay + ah, by
            if overlap > 0.4 and abs(a_bottom - b_top) < 0.25 * bh and ay < by:
                p.on = q.asset.id
                stacked.append(f"{p.asset.id} on {q.asset.id}")
                break
    # Resolve heights in dependency order.
    for _ in range(len(placed)):
        for p in placed:
            if p.on:
                base = by_id[p.on]
                p.x, p.y, p.z = base.x, base.y, base.z + base.H
    return " · ".join(stacked) if stacked else "No stacks seen"


def _resolve(placed: list[Placed]) -> str:
    moved = 0
    for _ in range(20):
        changed = False
        for i, p in enumerate(placed):
            for q in placed[i + 1:]:
                if p.on == q.asset.id or q.on == p.asset.id or abs(p.z - q.z) > 1e-6:
                    continue
                a, b = p.footprint(), q.footprint()
                ox = min(a[2], b[2]) - max(a[0], b[0])
                oy = min(a[3], b[3]) - max(a[1], b[1])
                if ox > -GAP_M and oy > -GAP_M:
                    shift = ox + GAP_M
                    q.x += shift if q.x >= p.x else -shift
                    for r in placed:            # carry anything stacked on q
                        if r.on == q.asset.id:
                            r.x = q.x
                    moved += 1
                    changed = True
        if not changed:
            break
    # Centre the arrangement on the origin.
    lo = np.min([p.footprint()[:2] for p in placed], axis=0)
    hi = np.max([p.footprint()[2:] for p in placed], axis=0)
    c = (lo + hi) / 2
    for p in placed:
        p.x -= c[0]
        p.y -= c[1]
    return f"{moved} nudge{'s' if moved != 1 else ''} to clear footprints" if moved else "No overlaps"


def _write_stage(path: Path, placed: list[Placed], floor: tuple[float, float]) -> None:
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr(9.81)

    # Floor: a thin static collider slab, top surface at z = 0.
    fl = UsdGeom.Cube.Define(stage, "/World/Floor")
    fl.CreateSizeAttr(1.0)
    fx = UsdGeom.XformCommonAPI(fl)
    fx.SetTranslate(Gf.Vec3d(0, 0, -0.025))
    fx.SetScale(Gf.Vec3f(floor[0], floor[1], 0.05))
    fl.CreateDisplayColorAttr([Gf.Vec3f(0.32, 0.30, 0.28)])
    UsdPhysics.CollisionAPI.Apply(fl.GetPrim())
    pmat = UsdShade.Material.Define(stage, "/World/Looks/floor_physics")
    pm = UsdPhysics.MaterialAPI.Apply(pmat.GetPrim())
    pm.CreateStaticFrictionAttr(FLOOR_FRICTION[0])
    pm.CreateDynamicFrictionAttr(FLOOR_FRICTION[1])
    UsdShade.MaterialBindingAPI.Apply(fl.GetPrim()).Bind(pmat, UsdShade.Tokens.weakerThanDescendants, "physics")

    # Light so the scene isn't black when opened.
    dome = UsdLux.DomeLight.Define(stage, "/World/Lights/Dome")
    dome.CreateIntensityAttr(600)
    sun = UsdLux.DistantLight.Define(stage, "/World/Lights/Key")
    sun.CreateIntensityAttr(2500)
    UsdGeom.XformCommonAPI(sun).SetRotate(Gf.Vec3f(-50, 0, 35))

    boxes = UsdGeom.Scope.Define(stage, "/World/Boxes")
    for p in placed:
        prim_path = f"{boxes.GetPath()}/{asset_name(p.asset.id)}"
        x = UsdGeom.Xform.Define(stage, prim_path)
        x.GetPrim().GetReferences().AddReference(f"./{p.asset.usd}")
        UsdGeom.XformCommonAPI(x).SetTranslate(Gf.Vec3d(p.x, p.y, p.z + DROP_M))
    stage.GetRootLayer().customLayerData = {"generator": "TE SimScrub", "placement": "estimated from hero frame"}
    stage.Save()


def _validate_stage(path: Path, placed: list[Placed]) -> list[Check]:
    stage = Usd.Stage.Open(str(path))
    checks: list[Check] = []

    def add(rule, label, ok, detail, warn=False):
        checks.append(Check(rule=rule, label=label, status="pass" if ok else ("warn" if warn else "fail"), detail=detail))

    add("units", "Meters, Z-up", UsdGeom.GetStageMetersPerUnit(stage) == 1.0 and UsdGeom.GetStageUpAxis(stage) == "Z",
        "metersPerUnit 1 · upAxis Z")
    scenes = [p for p in stage.Traverse() if p.IsA(UsdPhysics.Scene)]
    add("physics_scene", "Physics scene", bool(scenes), "gravity 9.81 m/s² down" if scenes else "missing")
    floor = stage.GetPrimAtPath("/World/Floor")
    add("floor", "Floor collider", floor.IsValid() and floor.HasAPI(UsdPhysics.CollisionAPI), "static slab, top at z=0")

    bbox = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    broken, bodies, below = [], 0, []
    boxes = []
    for p in placed:
        prim = stage.GetPrimAtPath(f"/World/Boxes/{asset_name(p.asset.id)}")
        if not prim.IsValid() or not prim.GetChildren():
            broken.append(p.asset.id)
            continue
        # SimReady assets carry the rigid body on Geometry/<name>, below the reference root.
        if any(q.HasAPI(UsdPhysics.RigidBodyAPI) for q in Usd.PrimRange(prim)):
            bodies += 1
        r = bbox.ComputeWorldBound(prim).ComputeAlignedRange()
        if r.GetMin()[2] < -1e-3:
            below.append(p.asset.id)
        boxes.append((p, r))
    add("references", "Asset references resolve", not broken,
        f"{len(placed) - len(broken)}/{len(placed)} loaded" + (f" · broken: {', '.join(broken)}" if broken else ""))
    add("bodies", "Rigid bodies", bodies == len(placed), f"{bodies}/{len(placed)} boxes will fall and collide")
    add("above_floor", "Nothing below the floor", not below, "all start above z=0" if not below else ", ".join(below))
    hits = []
    for i, (p, a) in enumerate(boxes):
        for q, b in boxes[i + 1:]:
            if p.on == q.asset.id or q.on == p.asset.id:
                continue
            ov = [min(a.GetMax()[k], b.GetMax()[k]) - max(a.GetMin()[k], b.GetMin()[k]) for k in range(3)]
            if min(ov) > 0.005:
                hits.append(f"{p.asset.id}/{q.asset.id}")
    add("overlaps", "No interpenetration", not hits, "boxes start apart" if not hits else ", ".join(hits))
    add("placement", "Placement source", True, "estimated from one video frame (no depth)")
    return checks


def _preview(path: Path, job_dir: Path, placed: list[Placed], floor: tuple[float, float]) -> None:
    """One GLB of the whole scene (Y-up for the browser) using each asset's viewer mesh."""
    scene = trimesh.Scene()
    slab = trimesh.creation.box(extents=(floor[0], 0.05, floor[1]))
    slab.apply_translation((0, -0.025, 0))
    slab.visual = trimesh.visual.ColorVisuals(slab, face_colors=[82, 77, 72, 255])
    scene.add_geometry(slab, node_name="floor")
    for p in placed:
        m = trimesh.load(job_dir / p.asset.clean_mesh, force="mesh")
        # USD (x, y, z) -> glTF (x, z, -y)
        m.apply_translation((p.x, p.z + DROP_M, -p.y))
        scene.add_geometry(m, node_name=p.asset.id)
    scene.export(path)


def assemble(job: Job, job_dir: Path, changed: Changed) -> None:
    steps = {n: Step(name=n) for n in STEP_NAMES}
    job.stage.steps = list(steps.values())
    job.stage.checks = []
    changed()

    def step(name: str, status: str, detail: str = "", more: str = "") -> None:
        steps[name].status, steps[name].detail = status, detail
        if more:
            steps[name].more = more
        changed()

    assets = [a for a in job.assets if not a.excluded and a.usd and a.dims]
    current = "Place boxes"
    try:
        if not assets:
            raise ValueError("No SimReady assets yet")
        step(current, "working")
        placed, how = _place(job, assets)
        step(current, "done", f"{len(placed)} boxes · {how}",
             f"Each box's bottom edge in the hero frame is projected onto the floor, assuming the phone was "
             f"held at {CAM_HEIGHT_M} m, tilted {CAM_PITCH_DEG:.0f}° down, ~{CAM_HFOV_DEG:.0f}° horizontal "
             "field of view. One frame has no depth, so this is an estimate: gravity does the rest.")

        current = "Stack"
        step(current, "done", _stack(placed),
             "A box whose bottom sits on another box's top in the hero frame (and overlaps it sideways) "
             "is placed on top of it.")

        current = "Resolve overlaps"
        step(current, "done", _resolve(placed),
             f"Boxes on the same level whose footprints collide are pushed apart with a {GAP_M * 100:.0f} cm gap, "
             "then the arrangement is centred on the origin.")

        current = "Floor + physics"
        span = np.max([np.abs(p.footprint()) for p in placed], axis=0)
        floor = (max(2.5, 2 * max(span[0], span[2]) + 2 * FLOOR_MARGIN_M),
                 max(2.5, 2 * max(span[1], span[3]) + 2 * FLOOR_MARGIN_M))
        step(current, "done", f"Floor {floor[0]:.1f} × {floor[1]:.1f} m · gravity 9.81 · dome + key light",
             f"Static floor slab with collision (friction {FLOOR_FRICTION[0]}/{FLOOR_FRICTION[1]}), "
             "UsdPhysics.Scene with gravity along −Z, a dome light and a distant key light.")

        current = "Write stage.usda"
        path = job_dir / "stage.usda"
        _write_stage(path, placed, floor)
        step(current, "done", f"stage.usda · {len(placed)} references",
             "Boxes are USD references to each asset file, not copies, so re-running SimReady updates the stage. "
             f"Each starts {DROP_M * 100:.0f} cm above its support and settles when physics starts.")

        current = "Validate stage"
        checks = _validate_stage(path, placed)
        job.stage.checks = checks
        bad = [c for c in checks if c.status != "pass"]
        step(current, "failed" if any(c.status == "fail" for c in bad) else "done",
             f"{len(checks) - len(bad)}/{len(checks)} checks pass")

        current = "Preview"
        _preview(job_dir / "stage_preview.glb", job_dir, placed, floor)
        step(current, "done", "stage_preview.glb")

        job.stage.usd = "stage.usda"
        job.stage.preview = "stage_preview.glb"
        job.stage.floor_m = list(floor)
        job.stage.total_mass_kg = round(sum(
            float(s.detail.split(" kg")[0]) for a in assets for s in a.prep
            if s.name == "Physics + label" and " kg" in s.detail), 2)
        job.stage.layout = [{"id": p.asset.id, "usd_name": asset_name(p.asset.id), "x": round(p.x, 3), "y": round(p.y, 3), "z": round(p.z, 3),
                             "on": p.on, "source": p.source} for p in placed]
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        step(current, "failed", chip_for(e) if not isinstance(e, ValueError) else str(e))
        for n in STEP_NAMES[STEP_NAMES.index(current) + 1:]:
            steps[n].status = "skipped"
        changed()
