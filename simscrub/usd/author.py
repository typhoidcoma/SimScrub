"""Author one SimReady box asset as USD.

Layout of each asset file (Z-up, meters, kilograms):

    /BX_01                  Xform, kind=component, RigidBodyAPI, MassAPI
        /geo                Mesh, CollisionAPI, MeshCollisionAPI(convexHull),
                            semantic label "amazon_box"
        /Looks/cardboard    UsdPreviewSurface (+ base colour texture)
        /Looks/cardboard_physics   UsdPhysics material (friction, restitution)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pxr import Gf, Kind, Sdf, Usd, UsdGeom, UsdPhysics, UsdSemantics, UsdShade, Vt

LABEL = "amazon_box"
CARDBOARD_RGB = (0.62, 0.47, 0.31)   # used when there is no texture (proxies)
CARDBOARD_ROUGHNESS = 0.85
FRICTION = (0.55, 0.45)              # static, dynamic: cardboard on wood/carpet
RESTITUTION = 0.1


@dataclass
class BoxAsset:
    name: str                  # BX_01 (USD-safe)
    vertices: np.ndarray       # (N, 3) meters, Z-up, base at z=0
    faces: np.ndarray          # (M, 3)
    uv: np.ndarray | None      # (N, 2) per-vertex UVs, or None
    texture: str | None        # file name relative to the USD file
    mass_kg: float


def write_box(path: Path, a: BoxAsset) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1.0)

    root = UsdGeom.Xform.Define(stage, f"/{a.name}")
    stage.SetDefaultPrim(root.GetPrim())
    Usd.ModelAPI(root.GetPrim()).SetKind(Kind.Tokens.component)

    # ---- geometry ----
    mesh = UsdGeom.Mesh.Define(stage, f"/{a.name}/geo")
    pts = a.vertices.astype(np.float32)
    mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(pts))
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray([3] * len(a.faces)))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(a.faces.astype(np.int32).ravel()))
    mesh.CreateExtentAttr([Gf.Vec3f(*pts.min(0).tolist()), Gf.Vec3f(*pts.max(0).tolist())])
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateDoubleSidedAttr(True)  # open flaps are single sheets
    if a.uv is not None:
        st = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                                                     UsdGeom.Tokens.faceVarying)
        st.Set(Vt.Vec2fArray.FromNumpy(a.uv[a.faces].reshape(-1, 2).astype(np.float32)))

    # ---- look ----
    looks = f"/{a.name}/Looks"
    mat = UsdShade.Material.Define(stage, f"{looks}/cardboard")
    surf = UsdShade.Shader.Define(stage, f"{looks}/cardboard/surface")
    surf.CreateIdAttr("UsdPreviewSurface")
    surf.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(CARDBOARD_ROUGHNESS)
    surf.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
    if a.texture and a.uv is not None:
        reader = UsdShade.Shader.Define(stage, f"{looks}/cardboard/st_reader")
        reader.CreateIdAttr("UsdPrimvarReader_float2")
        reader.CreateInput("varname", Sdf.ValueTypeNames.String).Set("st")
        tex = UsdShade.Shader.Define(stage, f"{looks}/cardboard/albedo")
        tex.CreateIdAttr("UsdUVTexture")
        tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(f"./{a.texture}")
        tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
        tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), "result")
        tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(tex.ConnectableAPI(), "rgb")
    else:
        surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*CARDBOARD_RGB))
    mat.CreateSurfaceOutput().ConnectToSource(surf.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(mat)

    # ---- physics ----
    UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
    UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr(float(a.mass_kg))
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr(UsdPhysics.Tokens.convexHull)

    pmat = UsdShade.Material.Define(stage, f"{looks}/cardboard_physics")
    pm = UsdPhysics.MaterialAPI.Apply(pmat.GetPrim())
    pm.CreateStaticFrictionAttr(FRICTION[0])
    pm.CreateDynamicFrictionAttr(FRICTION[1])
    pm.CreateRestitutionAttr(RESTITUTION)
    UsdShade.MaterialBindingAPI(mesh.GetPrim()).Bind(
        pmat, UsdShade.Tokens.weakerThanDescendants, "physics"
    )

    # ---- semantics: Isaac Sim 5 reads LabelsAPI; older replicator reads Semantics ----
    UsdSemantics.LabelsAPI.Apply(mesh.GetPrim(), "class").CreateLabelsAttr([LABEL])
    legacy = mesh.GetPrim()
    legacy.CreateAttribute("semantic:Semantics:params:semanticType", Sdf.ValueTypeNames.String).Set("class")
    legacy.CreateAttribute("semantic:Semantics:params:semanticData", Sdf.ValueTypeNames.String).Set(LABEL)

    stage.GetRootLayer().customLayerData = {"generator": "TE SimScrub", "asset": a.name}
    stage.Save()
