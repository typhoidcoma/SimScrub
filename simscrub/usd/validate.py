"""Asset validator: a registry of rules run against a USD file on disk.

Each rule opens nothing itself; it gets the stage and returns
(status, detail). New rules are one decorated function. Run from the CLI
with ``simscrub validate path/to/asset.usda``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from pxr import Usd, UsdGeom, UsdPhysics, UsdSemantics, UsdShade

from simscrub.manifest import Check
from simscrub.usd.author import LABEL

Rule = Callable[[Usd.Stage, Path], tuple[str, str]]
RULES: list[tuple[str, str, Rule]] = []


def rule(rid: str, label: str):
    def wrap(fn: Rule) -> Rule:
        RULES.append((rid, label, fn))
        return fn
    return wrap


def _mesh(stage: Usd.Stage) -> UsdGeom.Mesh | None:
    for p in stage.Traverse():
        if p.IsA(UsdGeom.Mesh):
            return UsdGeom.Mesh(p)
    return None


@rule("units", "Units in meters")
def _units(stage, _):
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    return ("pass", "metersPerUnit = 1") if abs(mpu - 1.0) < 1e-9 else ("fail", f"metersPerUnit = {mpu}")


@rule("up_axis", "Z-up axis")
def _up(stage, _):
    up = UsdGeom.GetStageUpAxis(stage)
    return ("pass", "upAxis = Z") if up == UsdGeom.Tokens.z else ("fail", f"upAxis = {up}")


@rule("default_prim", "Default prim set")
def _default(stage, _):
    p = stage.GetDefaultPrim()
    return ("pass", str(p.GetPath())) if p else ("fail", "No defaultPrim; references will break")


@rule("size", "Real-world size")
def _size(stage, _):
    m = _mesh(stage)
    if not m:
        return "fail", "No mesh"
    lo, hi = m.GetExtentAttr().Get()
    dims = [hi[i] - lo[i] for i in range(3)]
    txt = " × ".join(f"{d * 100:.0f}" for d in dims) + " cm"
    if not all(0.03 <= d <= 2.0 for d in dims):
        return "fail", f"{txt} is outside box range (3 cm–2 m)"
    if abs(lo[2]) > 0.005:
        return "warn", f"{txt}; base sits {lo[2] * 100:.1f} cm off the origin"
    return "pass", txt


@rule("collider", "Collider present")
def _collider(stage, _):
    m = _mesh(stage)
    if not m or not m.GetPrim().HasAPI(UsdPhysics.CollisionAPI):
        return "fail", "Mesh has no CollisionAPI"
    approx = UsdPhysics.MeshCollisionAPI(m.GetPrim()).GetApproximationAttr().Get()
    return "pass", f"approximation = {approx}"


@rule("rigid_body", "Rigid body + mass")
def _body(stage, _):
    root = stage.GetDefaultPrim()
    if not root or not root.HasAPI(UsdPhysics.RigidBodyAPI):
        return "fail", "No RigidBodyAPI on the asset root"
    mass = UsdPhysics.MassAPI(root).GetMassAttr().Get()
    if not mass or mass <= 0:
        return "fail", "Mass missing or zero"
    if not 0.05 <= mass <= 10:
        return "warn", f"{mass:.2f} kg looks wrong for an empty box"
    return "pass", f"{mass:.2f} kg"


@rule("material", "Material bound")
def _material(stage, path):
    m = _mesh(stage)
    if not m:
        return "fail", "No mesh"
    mat, _ = UsdShade.MaterialBindingAPI(m.GetPrim()).ComputeBoundMaterial()
    if not mat:
        return "fail", "No render material bound"
    for p in Usd.PrimRange(mat.GetPrim()):
        sh = UsdShade.Shader(p)
        if sh and sh.GetIdAttr().Get() == "UsdUVTexture":
            f = sh.GetInput("file").Get()
            tex = (path.parent / f.path).resolve()
            return ("pass", f"Texture {tex.name}") if tex.exists() else ("fail", f"Missing texture {f.path}")
    return "pass", "Flat cardboard colour"


@rule("label", "Semantic label")
def _label(stage, _):
    m = _mesh(stage)
    if not m or not m.GetPrim().HasAPI(UsdSemantics.LabelsAPI, "class"):
        return "fail", "No class label"
    labels = UsdSemantics.LabelsAPI(m.GetPrim(), "class").GetLabelsAttr().Get()
    return ("pass", f"class = {list(labels)[0]}") if labels and LABEL in labels else ("fail", f"labels = {labels}")


def validate(path: Path) -> list[Check]:
    stage = Usd.Stage.Open(str(path))
    out = []
    for rid, label, fn in RULES:
        try:
            status, detail = fn(stage, path)
        except Exception as e:  # noqa: BLE001 - a broken rule is a failed check, not a crash
            status, detail = "fail", f"Check errored: {e}"
        out.append(Check(rule=rid, label=label, status=status, detail=detail))
    return out
