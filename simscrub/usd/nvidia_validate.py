"""Run NVIDIA's official SimReady validator (simready-validate) on an asset.

The validator lives in its own Python 3.12 venv next to a clone of
github.com/NVIDIA/simready-foundation (its rules, features and profiles):

    %USERPROFILE%/.simscrub/simready-venv           SIMSCRUB_SIMREADY_VENV
    %USERPROFILE%/.simscrub/simready-foundation     SIMSCRUB_SIMREADY_FOUNDATION

``--stamp-asset-validation`` writes the result into the asset's own
customLayerData.SimReady_Metadata.validation, the way NVIDIA's sample assets
carry theirs.
"""

from __future__ import annotations

import ast
import datetime as _dt
import json
import os
import subprocess
import tempfile
from pathlib import Path

from simscrub.usd.author import PROFILE, PROFILE_VERSION

HOME = Path.home() / ".simscrub"
VENV = Path(os.environ.get("SIMSCRUB_SIMREADY_VENV", HOME / "simready-venv"))
FOUNDATION = Path(os.environ.get("SIMSCRUB_SIMREADY_FOUNDATION", HOME / "simready-foundation"))
EXE = VENV / "Scripts" / "simready-validate.exe"
STANDARD = "NVIDIA SimReady Foundation"

# Robotics-Prop 4.0.0: required features, with plain names for the UI.
REQUIRED = {
    "FET_000_STANDARD": "Core file layout",
    "FET_001_STANDARD": "Units, hierarchy, geometry",
    "FET_003_STANDARD": "Rigid body + colliders",
    "FET_006_STANDARD": "UsdPreviewSurface materials",
    "FET_011_STANDARD": "Semantic labels",
    "FET_011_RTX": "Semantic labels (RTX materials)",
    "FET_046_STANDARD": "Wikidata Q-Code",
    "FET_031_STANDARD": "Self-contained package",
    "FET_033_STANDARD": "Thumbnail + provenance metadata",
}
OPTIONAL = {
    "FET_004_STANDARD": "Multibody joints", "FET_005_STANDARD": "Grasp physics",
    "FET_006_MDL": "MDL materials", "FET_006_OPENPBR": "OpenPBR materials",
    "FET_007_STANDARD": "Non-visual sensor materials", "FET_010_STANDARD": "Display colour",
    "FET_100_ISAAC": "Isaac Sim composition",
    "FET_000_PHYSX": "PhysX runtime variant", "FET_003_PHYSX": "PhysX rigid bodies", "FET_004_PHYSX": "PhysX joints",
    "FET_000_NEWTON": "Newton runtime variant", "FET_003_NEWTON": "Newton rigid bodies", "FET_004_NEWTON": "Newton joints",
    "FET_000_MUJOCO": "MuJoCo runtime variant", "FET_003_MUJOCO": "MuJoCo rigid bodies", "FET_004_MUJOCO": "MuJoCo joints",
}


def available() -> bool:
    return EXE.exists() and (FOUNDATION / "sample_content" / "project_config.toml").exists()


def _list(v) -> list[str]:
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.startswith("["):
        try:
            return list(ast.literal_eval(v))
        except (ValueError, SyntaxError):
            return [v]
    return []


def _short(req: str) -> str:
    return req.replace("com.nvidia.simready.", "").replace("com.nvidia.usd.", "")


def validate(usd: Path, stamp: bool = True) -> dict:
    """Validate one asset; returns a compact, UI-ready summary."""
    if not available():
        return {"standard": STANDARD, "profile": PROFILE, "version": PROFILE_VERSION, "state": "unavailable",
                "detail": "simready-validate not installed"}
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "result.json"
        cmd = [str(EXE), "--project-config", "sample_content/project_config.toml",
               "--profile", PROFILE, "--version", PROFILE_VERSION, str(usd.resolve()), "--output", str(out)]
        if stamp:
            cmd.append("--stamp-asset-validation")
        r = subprocess.run(cmd, cwd=str(FOUNDATION), capture_output=True, text=True, timeout=300)
        if not out.exists():
            return {"standard": STANDARD, "profile": PROFILE, "version": PROFILE_VERSION, "state": "error",
                    "detail": (r.stderr or r.stdout).strip().splitlines()[-1:] or ["validator produced no report"]}
        report = next(iter(json.loads(out.read_text(encoding="utf-8")).values()))

    features = []
    for fid, f in sorted(report.get("features_summary", {}).items()):
        failing = [_short(x) for x in _list(f.get("failing requirements"))]
        unmet_optional = [_short(x) for x in _list(f.get("optional requirements"))]
        msgs = f.get("requirement_messages") or {}
        if isinstance(msgs, str):
            try:
                msgs = json.loads(msgs)
            except ValueError:
                msgs = {}
        required = fid in REQUIRED
        # NVIDIA's verdict is the source of truth: a feature only fails the
        # profile when the validator marks it blocking. A required feature with
        # nothing failing passes even if an optional sub-requirement is unmet.
        if f.get("blocking_failure") or (required and failing):
            status = "fail"
        elif f.get("passed") or required:
            status = "pass"
        else:
            status = "n/a"   # optional feature this asset doesn't implement
        features.append({
            "id": fid,
            "name": REQUIRED.get(fid) or OPTIONAL.get(fid) or fid.replace("_", " ").title(),
            "required": required,
            "status": status,
            "version": f.get("version", ""),
            "failing": failing,
            "unmet_optional": unmet_optional,
            "messages": {_short(k): v for k, v in msgs.items()} if isinstance(msgs, dict) else {},
        })
    req = [f for f in features if f["required"]]
    passed = not any(f["status"] == "fail" for f in features)
    return {
        "standard": STANDARD,
        "profile": report.get("profile_id", PROFILE),
        "version": report.get("profile_version", PROFILE_VERSION),
        "validator": f"simready-validate {report.get('simready_validate_version', '?')}",
        "state": "pass" if passed else "fail",
        "required_passed": sum(f["status"] == "pass" for f in req),
        "required_total": len(req),
        "optional_passed": sum(f["status"] == "pass" for f in features if not f["required"]),
        "features": features,
        "date": _dt.date.today().isoformat(),
        "stamped": stamp,
    }
