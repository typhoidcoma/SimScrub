"""Headless physics settle test. Runs in the Isaac Sim venv, not the app's.

    <isaac-venv>/python isaac/settle.py <stage.usda> <out.json> [seconds]

Opens the stage, records each box's pose, plays the simulation, and writes
where every box ended up: how far it dropped and slid, whether it is still
moving, and whether it fell through the floor. The SimScrub server reads the
JSON and shows it as the "Physics settle test" step.
"""

import json
import math
import sys
import time

from isaacsim import SimulationApp

# 8 GB laptop GPUs are below Isaac Sim's 10 GB minimum: use D3D12 (Vulkan crashed in
# rtx.scenedb on the RTX 5070 Laptop) and keep the renderer as light as possible.
# Physics is all this test needs.
app = SimulationApp({
    "headless": True,
    "width": 640,
    "height": 360,
    "anti_aliasing": 0,
    "extra_args": ["--/app/vulkan=false", "--/rtx/materialDb/syncLoads=true", "--/rtx-transient/resourcemanager/texturestreaming/memoryBudget=0.3"],
})

import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from pxr import Usd, UsdGeom  # noqa: E402

stage_path, out_path = sys.argv[1], sys.argv[2]
seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
t0 = time.time()

ctx = omni.usd.get_context()
ctx.open_stage(stage_path)
for _ in range(30):
    app.update()
stage = ctx.get_stage()


def poses() -> dict:
    out = {}
    root = stage.GetPrimAtPath("/World/Boxes")
    for p in root.GetChildren():
        m = UsdGeom.Xformable(p).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        t = m.ExtractTranslation()
        r = m.ExtractRotation()
        out[p.GetName()] = {"pos": [t[0], t[1], t[2]], "tilt_deg": _tilt(r)}
    return out


def _tilt(rot) -> float:
    """Angle between the box's up axis and world up."""
    up = rot.TransformDir((0, 0, 1))
    return math.degrees(math.acos(max(-1.0, min(1.0, up[2] / max(1e-9, up.GetLength())))))


start = poses()
timeline = omni.timeline.get_timeline_interface()
timeline.play()
fps = 60
prev = None
moving = {}
for i in range(int(seconds * fps)):
    app.update()
    if i == int(seconds * fps) - 10:
        prev = poses()
end = poses()
timeline.pause()

boxes = {}
for name, s in start.items():
    e = end[name]
    d = [e["pos"][k] - s["pos"][k] for k in range(3)]
    speed = math.dist(e["pos"], prev[name]["pos"]) * fps / 10 if prev else 0.0
    boxes[name] = {
        "start": s["pos"], "end": e["pos"],
        "drop_cm": round(-d[2] * 100, 1),
        "slide_cm": round(math.hypot(d[0], d[1]) * 100, 1),
        "tilt_deg": round(e["tilt_deg"], 1),
        "speed_cm_s": round(speed * 100, 2),
        "below_floor": e["pos"][2] < -0.05,
    }

result = {
    "seconds": seconds,
    "wall_s": round(time.time() - t0, 1),
    "boxes": boxes,
    "all_at_rest": all(b["speed_cm_s"] < 1.0 for b in boxes.values()),
    "none_fell_through": not any(b["below_floor"] for b in boxes.values()),
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2)
print("SIMSCRUB_SETTLE_OK", json.dumps({k: result[k] for k in ("all_at_rest", "none_fell_through", "wall_s")}))
app.close()
