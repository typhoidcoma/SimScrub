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
from simscrub.usd.author import QCODE

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
    bodies = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.RigidBodyAPI)]
    if not bodies:
        return "fail", "No prim has RigidBodyAPI"
    mass = UsdPhysics.MassAPI(bodies[0]).GetMassAttr().Get()
    if not mass or mass <= 0:
        return "fail", "Mass missing or zero"
    if not 0.05 <= mass <= 10:
        return "warn", f"{mass:.2f} kg looks wrong for an empty box"
    return "pass", f"{mass:.2f} kg on {bodies[0].GetName()}"


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


@rule("label", "Wikidata labels")
def _label(stage, _):
    for p in stage.Traverse():
        if p.HasAPI(UsdSemantics.LabelsAPI, "wikidata_qcode"):
            q = UsdSemantics.LabelsAPI(p, "wikidata_qcode").GetLabelsAttr().Get() or []
            if QCODE in q:
                cls = UsdSemantics.LabelsAPI(p, "wikidata_class").GetLabelsAttr().Get() or []
                return "pass", f"{QCODE} · {', '.join(cls) or 'no class'}"
            return "fail", f"wikidata_qcode = {list(q)}"
    if any(p.HasAttribute("semantic:Semantics:params:semanticData") for p in stage.Traverse()):
        return "fail", "Only the deprecated Semantics schema"
    return "fail", "No wikidata_qcode label"


@rule("asset_info", "Component + assetInfo")
def _asset_info(stage, _):
    root = stage.GetDefaultPrim()
    kind = Usd.ModelAPI(root).GetKind() if root else None
    info = root.GetAssetInfo() if root else {}
    if kind != "component":
        return "fail", f"kind = {kind or 'none'}"
    if "identifier" not in info or "name" not in info:
        return "fail", "assetInfo needs identifier + name"
    return "pass", f"{info['name']} v{info.get('version', '?')}"


@rule("metadata", "SimReady metadata")
def _metadata(stage, _):
    need = ["author", "asset_name", "asset_type", "asset_license", "category", "source_file",
            "usd_date_generated", "qcode", "rigid_body_count", "asset_extents", "mass"]
    md = stage.GetRootLayer().customLayerData.get("SimReady_Metadata", {})
    missing = [k for k in need if k not in md]
    return ("pass", f"{len(need)} provenance fields") if not missing else ("fail", "missing " + ", ".join(missing))


@rule("thumbnail", "Thumbnail 256×256")
def _thumb(stage, path):
    t = path.parent / ".thumbs" / "256x256" / f"{path.name}.png"
    if not t.exists():
        return "fail", f".thumbs/256x256/{path.name}.png missing"
    from PIL import Image

    with Image.open(t) as im:
        return ("pass", ".thumbs/256x256") if im.size == (256, 256) else ("fail", f"{im.size[0]}x{im.size[1]}")


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
