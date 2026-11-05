"""Heavy mesh operations (MeshLab), each run in its own child process.

MeshLab is native code: a crash must not take the server down, and it isn't
safe to call from several threads at once. Every op here reads and writes
OBJ files in a per-asset work folder (the texture PNG sits beside them) and
prints a JSON line of stats, which the SimReady stage shows to the artist.

    python -m simscrub.stages.meshops repair   <in.obj> <out.obj>
    python -m simscrub.stages.meshops decimate <in.obj> <out.obj> <faces>
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from simscrub.errors import MeshUnusable

_lock = threading.Lock()


def run_isolated(op: str, *args: str, timeout: float = 900) -> dict:
    """Run one op in a child process, one at a time; return its stats."""
    with _lock:
        r = subprocess.run(
            [sys.executable, "-m", "simscrub.stages.meshops", op, *map(str, args)],
            capture_output=True, text=True, timeout=timeout,
        )
    if r.returncode != 0:
        raise MeshUnusable(f"{op} exit {r.returncode}: {r.stderr[-400:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def _topology(ms) -> dict:
    t = ms.get_topological_measures()
    return {
        "faces": ms.current_mesh().face_number(),
        "verts": ms.current_mesh().vertex_number(),
        "pieces": int(t.get("connected_components_number", 0)),
        "holes": int(t.get("number_holes", -1)),
    }


def repair(src: Path, dst: Path) -> dict:
    """Remove floating fragments, duplicate/degenerate geometry. Reports each."""
    import pymeshlab

    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(str(src))
    before = _topology(ms)

    ms.meshing_remove_duplicate_vertices()
    after_dupv = ms.current_mesh().vertex_number()
    ms.meshing_remove_duplicate_faces()
    ms.meshing_remove_null_faces()
    after_degen = ms.current_mesh().face_number()
    # Count pieces only now: merging duplicate verts stitches split pieces back
    # together, which must not be reported as floaters removed.
    mid_pieces = int(ms.get_topological_measures().get("connected_components_number", 0))

    # Floaters: anything under 1% of the mesh is reconstruction noise.
    min_faces = max(50, before["faces"] // 100)
    ms.meshing_remove_connected_component_by_face_number(mincomponentsize=min_faces, removeunref=True)
    ms.meshing_remove_unreferenced_vertices()
    after = _topology(ms)

    ms.save_current_mesh(str(dst))
    return {
        "before": before,
        "after": after,
        "duplicate_verts": before["verts"] - after_dupv,
        "degenerate_faces": before["faces"] - after_degen,
        "stitched_pieces": max(0, before["pieces"] - mid_pieces),
        "floaters_removed": max(0, mid_pieces - after["pieces"]),
        "floater_faces": after_degen - after["faces"],
    }


def decimate(src: Path, dst: Path, faces: int) -> dict:
    """Texture-preserving quadric edge collapse down to roughly ``faces``."""
    import pymeshlab

    ms = pymeshlab.MeshSet()
    ms.load_new_mesh(str(src))
    before = ms.current_mesh().face_number()
    if before > faces:
        if ms.current_mesh().has_wedge_tex_coord():
            ms.meshing_decimation_quadric_edge_collapse_with_texture(
                targetfacenum=faces, preserveboundary=True, optimalplacement=True
            )
        else:
            ms.meshing_decimation_quadric_edge_collapse(targetfacenum=faces, preserveboundary=True)
    ms.save_current_mesh(str(dst))
    return {"before": before, "after": ms.current_mesh().face_number(), "target": faces}


if __name__ == "__main__":
    op = sys.argv[1]
    if op == "repair":
        out = repair(Path(sys.argv[2]), Path(sys.argv[3]))
    elif op == "decimate":
        out = decimate(Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]))
    else:
        raise SystemExit(f"unknown op {op}")
    print(json.dumps(out))
