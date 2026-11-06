"""Runs inside Isaac Sim (Kit --exec): open the SimScrub stage and take commands from the app.

The server passes everything through environment variables (no shell quoting):
    SIMSCRUB_STAGE    absolute path to stage.usda
    SIMSCRUB_STATUS   JSON status this script writes, which the app polls
    SIMSCRUB_CONTROL  JSON command file the app writes: {"id": n, "cmd": "play|stop|drop", "lift": 0.4}
    SIMSCRUB_PLAY     "1" to start the simulation once the stage is open

"drop" resets the simulation, lifts every box, and plays, so the boxes visibly
fall onto the floor (and onto each other). It edits only the session layer:
the stage file on disk is never changed.
"""

import asyncio
import json
import math
import os
import time

import carb
import omni.kit.app
import omni.timeline
import omni.usd
from pxr import Gf, Usd, UsdGeom

STAGE = os.environ.get("SIMSCRUB_STAGE", "")
STATUS = os.environ.get("SIMSCRUB_STATUS", "")
CONTROL = os.environ.get("SIMSCRUB_CONTROL", "")
PLAY = os.environ.get("SIMSCRUB_PLAY") == "1"

_last_cmd = -1
_boxes: list[str] = []


def report(state: str, detail: str = "") -> None:
    carb.log_warn(f"[simscrub] {state} {detail}")
    if STATUS:
        with open(STATUS, "w", encoding="utf-8") as f:
            json.dump({"state": state, "detail": detail, "stage": STAGE, "t": time.time()}, f)


def _frame_camera(stage) -> None:
    """Side-on three-quarter view that fits every box in frame.

    The boxes are spread along some direction on the floor; the camera looks at
    that line from the side (perpendicular), pulled back to fit the whole spread.
    """
    import math

    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    pts = []
    for p in stage.GetPrimAtPath("/World/Boxes").GetChildren():
        c = cache.ComputeWorldBound(p).ComputeAlignedRange().GetMidpoint()
        pts.append((c[0], c[1]))
    # Centre on the middle of the whole spread, not the average box (which leans
    # toward wherever boxes cluster and pushes the others out of frame).
    cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
    cy = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
    # principal direction of the spread (2x2 covariance, closed form)
    sxx = sum((p[0] - cx) ** 2 for p in pts); syy = sum((p[1] - cy) ** 2 for p in pts)
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in pts)
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    ax, ay = math.cos(ang), math.sin(ang)
    px, py = -ay, ax                       # perpendicular: the camera side
    if py > 0:                             # prefer viewing from the -Y side
        px, py = -px, -py
    spread = max(math.hypot(p[0] - cx, p[1] - cy) for p in pts) * 2 + 0.8
    dist = max(1.4, spread * 0.85)         # fits the whole spread with margin
    target = Gf.Vec3d(cx, cy, 0.12)
    # lean the view a little along the spread for a three-quarter angle
    eye = target + Gf.Vec3d((px + 0.35 * ax) * dist, (py + 0.35 * ay) * dist, 0.42 * dist)
    try:
        from isaacsim.core.utils.viewports import set_camera_view

        set_camera_view(eye=list(eye), target=list(target))
    except Exception as e:  # noqa: BLE001 - fall back to framing the selection
        carb.log_warn(f"[simscrub] set_camera_view failed ({e}); framing selection")
        from omni.kit.viewport.utility import frame_viewport_selection, get_active_viewport

        ctx = omni.usd.get_context()
        ctx.get_selection().set_selected_prim_paths(["/World/Boxes"], True)
        frame_viewport_selection(get_active_viewport())
        ctx.get_selection().clear_selected_prim_paths()


PILE = "/World/Pile"


def _clear_pile(stage) -> None:
    """Remove the pile and bring the placed boxes back (session layer only)."""
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        if stage.GetPrimAtPath(PILE).IsValid():
            stage.RemovePrim(PILE)
        for path in _boxes:
            stage.GetPrimAtPath(path).SetActive(True)


def _pile_camera(cx: float, cy: float) -> None:
    from isaacsim.core.utils.viewports import set_camera_view

    # Aim at mid-tower: the 2 m tower fits on the way down, the heap fills the lower half.
    set_camera_view(eye=[cx + 2.1, cy - 3.5, 1.7], target=[cx, cy, 0.75])


async def _pile(count: int) -> None:
    """Hide the placed boxes; drop ``count`` copies of them as a tower onto stage centre."""
    import random

    tl = omni.timeline.get_timeline_interface()
    tl.stop()
    for _ in range(5):
        await omni.kit.app.get_app().next_update_async()
    stage = omni.usd.get_context().get_stage()
    _clear_pile(stage)
    root = stage.GetRootLayer()
    stage_dir = os.path.dirname(STAGE)
    # Asset files each placed box references (stage-relative in the file; absolute here,
    # because the session layer has no folder to resolve relative paths against).
    assets = []
    for path in _boxes:
        spec = root.GetPrimAtPath(path)
        refs = list(spec.referenceList.prependedItems) if spec else []
        if refs:
            assets.append(os.path.normpath(os.path.join(stage_dir, refs[0].assetPath)))
    if not assets:
        report("failed", "no box assets to pile")
        return
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    pts = [cache.ComputeWorldBound(stage.GetPrimAtPath(p)).ComputeAlignedRange().GetMidpoint() for p in _boxes]
    cx = (min(p[0] for p in pts) + max(p[0] for p in pts)) / 2
    cy = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2

    _pile_camera(cx, cy)              # move first, so the tower appears already framed
    rng = random.Random(7)            # same pile every time: repeatable demo takes
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for path in _boxes:
            stage.GetPrimAtPath(path).SetActive(False)
        UsdGeom.Scope.Define(stage, PILE)
        per_layer = 4
        for i in range(count):
            layer, slot = divmod(i, per_layer)
            ang = math.radians(90 * slot + 45 * (layer % 2) + rng.uniform(-15, 15))
            r = 0.26 + rng.uniform(-0.05, 0.08)
            x = UsdGeom.Xform.Define(stage, f"{PILE}/box_{i + 1:02d}")
            x.GetPrim().GetReferences().AddReference(assets[i % len(assets)])
            xf = UsdGeom.XformCommonAPI(x)
            xf.SetTranslate(Gf.Vec3d(cx + r * math.cos(ang), cy + r * math.sin(ang), 0.5 + 0.42 * layer))
            xf.SetRotate(Gf.Vec3f(rng.uniform(-25, 25), rng.uniform(-25, 25), rng.uniform(0, 360)))
    for _ in range(40):
        await omni.kit.app.get_app().next_update_async()
    _pile_camera(cx, cy)              # again: loading new references can reset the view
    for _ in range(40):               # hold the tower on screen for a beat, then let go
        await omni.kit.app.get_app().next_update_async()
    tl.play()
    report("dropping", f"pile of {count} boxes")


async def _drop(lift: float) -> None:
    tl = omni.timeline.get_timeline_interface()
    tl.stop()                      # physics resets bodies to their authored poses
    for _ in range(5):
        await omni.kit.app.get_app().next_update_async()
    stage = omni.usd.get_context().get_stage()
    if stage.GetPrimAtPath(PILE).IsValid():
        _clear_pile(stage)
        _frame_camera(stage)
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for path in _boxes:
            xf = UsdGeom.XformCommonAPI(stage.GetPrimAtPath(path))
            t, *_ = xf.GetXformVectors(Usd.TimeCode.Default())
            base = _base_z.setdefault(path, t[2])
            xf.SetTranslate(Gf.Vec3d(t[0], t[1], base + lift))
    for _ in range(3):
        await omni.kit.app.get_app().next_update_async()
    tl.play()
    report("dropping", f"{len(_boxes)} boxes from +{lift * 100:.0f} cm")


_base_z: dict[str, float] = {}


def _present(on: bool = True) -> None:
    """Viewport only, full screen: for screen recordings and demos."""
    st = carb.settings.get_settings()
    st.set("/app/window/hideUi", bool(on))
    # True full screen (no title bar or taskbar): ask the app window directly.
    try:
        import omni.appwindow

        win = omni.appwindow.get_default_app_window()
        if hasattr(win, "set_fullscreen"):
            win.set_fullscreen(bool(on))
        else:
            st.set("/app/window/fullscreen", bool(on))
    except Exception as e:  # noqa: BLE001
        carb.log_warn(f"[simscrub] fullscreen failed: {e}")
        st.set("/app/window/fullscreen", bool(on))
    report("presenting" if on else "opened", f"{len(_boxes)} boxes")


def _on_update(_e) -> None:
    global _last_cmd
    if not CONTROL or not os.path.exists(CONTROL):
        return
    try:
        with open(CONTROL, encoding="utf-8") as f:
            cmd = json.load(f)
    except (OSError, ValueError):
        return
    if cmd.get("id", -1) == _last_cmd:
        return
    _last_cmd = cmd.get("id", -1)
    tl = omni.timeline.get_timeline_interface()
    c = cmd.get("cmd")
    if c == "play":
        tl.play(); report("playing", f"{len(_boxes)} boxes")
    elif c == "stop":
        tl.stop(); report("opened", f"{len(_boxes)} boxes")
    elif c == "drop":
        asyncio.ensure_future(_drop(float(cmd.get("lift", 0.4))))
    elif c == "pile":
        asyncio.ensure_future(_pile(int(cmd.get("count", 16))))
    elif c == "present":
        _present(cmd.get("on", True))


async def _open() -> None:
    try:
        for _ in range(30):
            await omni.kit.app.get_app().next_update_async()
        report("opening", STAGE)
        ctx = omni.usd.get_context()
        ok, err = await ctx.open_stage_async(STAGE)
        if not ok:
            report("failed", f"could not open stage: {err}")
            return
        for _ in range(20):
            await omni.kit.app.get_app().next_update_async()
        stage = ctx.get_stage()
        _boxes.extend(str(p.GetPath()) for p in stage.GetPrimAtPath("/World/Boxes").GetChildren())
        _frame_camera(stage)
        report("opened", f"{len(_boxes)} boxes")
        global _sub
        _sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(_on_update)
        if PLAY:
            omni.timeline.get_timeline_interface().play()
            report("playing", f"{len(_boxes)} boxes")
    except Exception as e:  # noqa: BLE001
        report("failed", repr(e))


_sub = None
if not STAGE:
    report("failed", "SIMSCRUB_STAGE not set")
else:
    asyncio.ensure_future(_open())
