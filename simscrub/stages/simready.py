"""Stage 4: clean each raw mesh into a SimReady USD asset, one visible step at a time.

    1 Repair         floaters, duplicate verts, degenerate faces   (MeshLab, isolated)
    2 Decimate       ~1.4M -> ~20k tris, texture kept               (MeshLab, isolated)
    3 Orient         Z-up, sides aligned to X/Y, base on z = 0
    4 Scale          real size in meters: tape measurement, else best guess from proportions
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
from simscrub.sizing import guess_size
from simscrub.stages import meshops
from simscrub.usd.author import (
    CARDBOARD_RGB, CARDBOARD_ROUGHNESS, FRICTION, LABEL, MATERIAL_QCODE, QCODE, BoxAsset, asset_name, write_box,
)
from simscrub.usd import nvidia_validate as nv
from simscrub.usd.validate import validate

Changed = Callable[[Asset], None]

TARGET_TRIS = 20_000
CARDBOARD_GSM = 550           # g/m², single-wall corrugated

STEP_NAMES = ["Repair", "Decimate", "Orient", "Scale to real size", "Collider",
              "Physics + label", "Write USD", "Validate", "NVIDIA SimReady validation"]

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


def _outward(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray, int]:
    """Counter-clockwise, outward-facing triangles (VG.029) and unit faceVarying normals (VG.028).

    UV seams split vertices, so winding and normals are computed on a copy with
    seams merged, then mapped back face-for-face to the textured mesh.
    """
    merged = trimesh.Trimesh(vertices=mesh.vertices.copy(), faces=mesh.faces.copy(), process=False)
    merged.merge_vertices(merge_tex=True, merge_norm=True)
    before = merged.face_normals.copy()
    trimesh.repair.fix_winding(merged)
    trimesh.repair.fix_inversion(merged)
    flip = np.einsum("ij,ij->i", before, merged.face_normals) < 0
    faces = np.asarray(mesh.faces).copy()
    faces[flip] = faces[flip][:, ::-1]
    n = merged.vertex_normals[merged.faces].reshape(-1, 3)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    return faces, n, int(flip.sum())


def _display_rgb(img) -> tuple[float, float, float]:
    """displayColor fallback (DISP.001): average of the texture, else flat cardboard."""
    if img is None:
        return CARDBOARD_RGB
    a = np.asarray(img.convert("RGB").resize((64, 64))).reshape(-1, 3) / 255.0
    return tuple(float(v) for v in np.clip(a.mean(0), 0, 1))


def _thumbnail(glb: Path, png: Path) -> bool:
    """Render with the same headless viewer the app uses (ui/scripts/thumbnail.mjs)."""
    import subprocess

    ui = Path(__file__).resolve().parents[2] / "ui"
    try:
        r = subprocess.run(["node", str(ui / "scripts" / "thumbnail.mjs"), str(glb.resolve()), str(png.resolve())],
                           cwd=str(ui), capture_output=True, text=True, timeout=120, shell=False)
        return r.returncode == 0 and png.exists()
    except (OSError, subprocess.TimeoutExpired):
        return False


def _target_dims(a: Asset, all_assets: list[Asset], cur: np.ndarray) -> tuple[np.ndarray, str, str]:
    """Target size in meters (x=long, y=short, z=height), source, and a sentence."""
    if a.measured:
        lw = sorted([a.measured.l, a.measured.w], reverse=True)
        t = np.array([lw[0], lw[1], a.measured.h]) / 100
        return t, "tape", "tape measurement"
    # Best guess: closest common carton by proportions; uniform scale keeps the
    # mesh's own shape. Measured boxes in the job, if any, anchor the size.
    longs = [max(b.measured.l, b.measured.w) / 100 for b in all_assets if b.measured and not b.excluded]
    centre = float(np.median(longs)) if longs else None
    g = guess_size(cur, centre)
    ratio = " : ".join(f"{v / cur[0]:.2f}" for v in cur)
    why = f"best guess: proportions {ratio} closest to a common {g.label} carton"
    if centre:
        why += f" (sized near your measured boxes)"
    return cur * (g.long_m / cur[0]), "inferred" if centre else "estimated", why


def prepare(a: Asset, assets: list[Asset], job_dir: Path, changed: Changed, job=None) -> None:
    steps = {n: Step(name=n) for n in STEP_NAMES}
    a.prep = list(steps.values())
    a.checks = []
    changed(a)

    def step(name: str, status: str, detail: str = "", more: str = "") -> None:
        steps[name].status, steps[name].detail = status, detail
        if more:
            steps[name].more = more
        changed(a)

    work = job_dir / "work" / a.id
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    name = asset_name(a.id)
    out_dir = job_dir / "assets" / name / "simready_usd"     # NP.005: <root>/<intermediate>/<main>
    shutil.rmtree(out_dir.parent, ignore_errors=True)
    shutil.rmtree(job_dir / "assets" / a.id, ignore_errors=True)   # pre-spec folder layout
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
            short = (f"{r['floaters_removed']} floaters removed" if r["floaters_removed"] else "No floaters")
            short += f" · {_fmt(r['duplicate_verts'])} dup verts merged"
            step("Repair", "done", short, " · ".join(bits))

            current = "Decimate"
            step("Decimate", "working", f"{_fmt(r['after']['faces'])} → ~{_fmt(TARGET_TRIS)} tris, keeping UVs")
            d = meshops.run_isolated("decimate", work / "repaired.obj", work / "decimated.obj", TARGET_TRIS)
            pct = 100 * (1 - d["after"] / max(1, d["before"]))
            step("Decimate", "done", f"{_fmt(d['before'])} → {_fmt(d['after'])} tris (−{pct:.1f}%)",
                 "Texture-aware quadric edge collapse (MeshLab). UV seams are preserved so the texture "
                 "still lands in the same places; the target is 20k triangles.")
            mesh = _load(work / "decimated.obj")

        # ---- 3: orient ----
        current = "Orient"
        step("Orient", "working")
        full = _orient(mesh)
        short = " · ".join(p for p in full.split(" · ") if not p.startswith(("Z-up", "base on")))
        step("Orient", "done", short.replace("levelled", "Levelled").replace("kept", "Kept"),
             full + ". glTF is Y-up; the USD stage is Z-up, so the mesh is rotated, its box faces aligned "
             "to X/Y with the long side on X, and its base moved to the ground plane.")

        # ---- 4: scale ----
        current = "Scale to real size"
        cur = mesh.extents.copy()
        target, src, why = _target_dims(a, assets, cur)
        s = target / cur
        S = np.diag([*s, 1.0])
        mesh.apply_transform(S)
        a.dims = Dims(l=round(target[0] * 100, 1), w=round(target[1] * 100, 1), h=round(target[2] * 100, 1))
        a.dims_source = src
        before = " × ".join(f"{v:.2f}" for v in cur)
        after = " × ".join(f"{v * 100:.1f}" for v in target)
        tag = "tape" if src == "tape" else why.split("common ")[-1].replace(" carton", "").replace(" (sized near your measured boxes)", "")
        step("Scale to real size", "done",
             f"{after} cm · " + ("tape" if src == "tape" else f"≈ {tag}"),
             f"{before} units → {after} cm · {why}")

        # ---- 5: collider ----
        current = "Collider"
        hull = mesh.convex_hull
        step("Collider", "done", f"Convex hull · {len(hull.faces)} tris · {hull.volume * 1000:.1f} L",
             "UsdPhysics MeshCollisionAPI with approximation=convexHull. PhysX cooks the same hull at load, "
             "so the collision shape matches what is reported here.")

        # ---- 6: physics + label ----
        current = "Physics + label"
        L, W, H = target
        area = 2 * (L * W + L * H + W * H)
        mass = area * CARDBOARD_GSM / 1000
        step("Physics + label", "done", f"{mass:.2f} kg · friction {FRICTION[0]} · {QCODE} cardboard box",
             f"Rigid body. Mass = surface {area:.2f} m² × {CARDBOARD_GSM} g/m² corrugated cardboard "
             f"(an empty box). Physics material: static friction {FRICTION[0]}, dynamic {FRICTION[1]}. "
             f"Semantic labels via UsdSemantics LabelsAPI: wikidata_class 'cardboard box', wikidata_qcode {QCODE}; "
             f"physics material labelled {MATERIAL_QCODE} (corrugated fiberboard).")

        # ---- 7: write USD (+ viewer GLB) ----
        current = "Write USD"
        step("Write USD", "working")
        texture = None
        img = getattr(getattr(mesh.visual, "material", None), "image", None)
        if img is None:
            img = getattr(getattr(mesh.visual, "material", None), "baseColorTexture", None)
        uv = getattr(mesh.visual, "uv", None)
        if img is not None and uv is not None and not proxy:
            texture = f"textures/{name}_albedo.png"
            (out_dir / "textures").mkdir()
            img.convert("RGB").save(out_dir / texture)
        faces, normals, flipped = _outward(mesh)
        display = _display_rgb(img if texture else None)
        usd = out_dir / f"{name}.usda"
        write_box(usd, BoxAsset(
            name=name, vertices=np.asarray(mesh.vertices), faces=faces, normals=normals,
            uv=np.asarray(uv) if texture else None, texture=texture, display_rgb=display,
            mass_kg=mass, dims_m=(float(target[0]), float(target[1]), float(target[2])),
            meta={
                "source_file": f"../../../{a.crop}" if a.crop else "",
                "source_video": (job.capture.video or "") if job else "",
                "pipeline_id": a.id,
                "reconstruction": "tripo" if not proxy else "proxy cuboid",
                "tripo_task_id": a.recon.task_id or "",
                "size_source": a.dims_source or "",
                "project_label": LABEL,
            },
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
        viewer_dir = job_dir / "viewer"
        viewer_dir.mkdir(exist_ok=True)
        view.export(viewer_dir / f"{name}.glb")
        a.usd = f"assets/{name}/simready_usd/{usd.name}"
        a.clean_mesh = f"viewer/{name}.glb"
        a.clean_tris = int(len(mesh.faces))
        # SR.002: 256x256 thumbnail next to the asset, rendered like the in-app viewer.
        thumb = out_dir / ".thumbs" / "256x256" / f"{usd.name}.png"
        thumb.parent.mkdir(parents=True, exist_ok=True)
        thumbs_ok = _thumbnail(viewer_dir / f"{name}.glb", thumb)
        a.thumb = f"assets/{name}/simready_usd/.thumbs/256x256/{usd.name}.png" if thumbs_ok else None
        size = sum(f.stat().st_size for f in out_dir.rglob("*") if f.is_file()) / 1e6
        step("Write USD", "done", f"{name}/simready_usd/{usd.name} · {size:.1f} MB",
             f"SimReady package {name}/simready_usd/: {usd.name} + textures/ + .thumbs/256x256/ · "
             f"{_fmt(len(mesh.faces))} tris · faceVarying normals ({flipped} faces re-wound to face outward) · "
             f"{'2048px sRGB texture' if texture else 'flat cardboard colour'} · "
             f"labels wikidata_class 'cardboard box', wikidata_qcode {QCODE}, material {MATERIAL_QCODE} · "
             f"SimReady_Metadata provenance block · Z-up, meters, kg")

        # ---- 8: validate the file on disk ----
        current = "Validate"
        step("Validate", "working", "Opening the written file")
        a.checks = validate(usd)
        # The file can't know where its size came from; the pipeline does.
        # Informational, never a warning: a best guess is a valid answer.
        a.checks.append(Check(
            rule="scale_source", label="Size source",
            status="pass",
            detail="Tape measurement" if a.dims_source == "tape" else "Best guess from proportions",
        ))
        n_pass = sum(c.status == "pass" for c in a.checks)
        bad = [c for c in a.checks if c.status in ("warn", "fail")]
        step("Validate", "failed" if any(c.status == "fail" for c in bad) else "done",
             f"{n_pass}/{len(a.checks)} checks pass" + (f" · {len(bad)} need a look" if bad else ""))

        # ---- 9: NVIDIA's official validator, result stamped into the asset ----
        current = "NVIDIA SimReady validation"
        step(current, "working", f"simready-validate · {nv.PROFILE} {nv.PROFILE_VERSION}")
        a.spec = nv.validate(usd, stamp=True)
        if a.spec["state"] == "unavailable":
            step(current, "skipped", "Validator not installed",
                 "Install simready-validate (Python 3.12) to check against NVIDIA's SimReady spec.")
        elif a.spec["state"] == "error":
            step(current, "failed", "Validator error", str(a.spec.get("detail")))
        else:
            sp = a.spec
            fails = [f for f in sp["features"] if f["status"] == "fail"]
            step(current, "done" if sp["state"] == "pass" else "failed",
                 f"{'PASS' if sp['state'] == 'pass' else 'FAIL'} · {sp['profile']} {sp['version']} · "
                 f"{sp['required_passed']}/{sp['required_total']} required features",
                 f"{sp['standard']}, profile {sp['profile']} v{sp['version']}, checked by {sp['validator']}. "
                 + (f"Failing: " + "; ".join(f"{f['id']} {f['name']}: {', '.join(f['failing'])}" for f in fails)
                    if fails else "Every required feature passes; result stamped into the asset's "
                                  "SimReady_Metadata.validation."))

        # Intermediate OBJs (hundreds of MB) are only needed for debugging a failure.
        shutil.rmtree(work, ignore_errors=True)
    except Exception as e:  # noqa: BLE001 - show which step failed, in plain words
        traceback.print_exc()  # full detail for the developer log; the artist sees the chip
        step(current, "failed", chip_for(e))
        for n in STEP_NAMES[STEP_NAMES.index(current) + 1:]:
            steps[n].status = "skipped"
        changed(a)
