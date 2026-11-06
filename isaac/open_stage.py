"""Runs inside Isaac Sim (Kit --exec): open the SimScrub stage, ready to press Play.

The server passes everything through environment variables (no shell quoting):
    SIMSCRUB_STAGE   absolute path to stage.usda
    SIMSCRUB_STATUS  where to write a small JSON status the app polls
    SIMSCRUB_PLAY    "1" to start the simulation once the stage is open
"""

import asyncio
import json
import os
import time

import carb
import omni.kit.app
import omni.timeline
import omni.usd

STAGE = os.environ.get("SIMSCRUB_STAGE", "")
STATUS = os.environ.get("SIMSCRUB_STATUS", "")
PLAY = os.environ.get("SIMSCRUB_PLAY") == "1"


def report(state: str, detail: str = "") -> None:
    carb.log_warn(f"[simscrub] {state} {detail}")
    if STATUS:
        with open(STATUS, "w", encoding="utf-8") as f:
            json.dump({"state": state, "detail": detail, "stage": STAGE, "t": time.time()}, f)


async def _open() -> None:
    try:
        # Let the full app finish loading before swapping stages.
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
        try:
            from omni.kit.viewport.utility import frame_viewport_selection, get_active_viewport

            ctx.get_selection().set_selected_prim_paths(["/World/Boxes"], True)
            frame_viewport_selection(get_active_viewport())
            ctx.get_selection().clear_selected_prim_paths()
        except Exception as e:  # noqa: BLE001 - framing is a nicety
            carb.log_warn(f"[simscrub] could not frame boxes: {e}")
        n = len(ctx.get_stage().GetPrimAtPath("/World/Boxes").GetChildren())
        report("opened", f"{n} boxes")
        if PLAY:
            omni.timeline.get_timeline_interface().play()
            report("playing", f"{n} boxes")
    except Exception as e:  # noqa: BLE001
        report("failed", repr(e))


if not STAGE:
    report("failed", "SIMSCRUB_STAGE not set")
else:
    asyncio.ensure_future(_open())
