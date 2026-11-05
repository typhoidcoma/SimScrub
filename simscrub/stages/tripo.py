"""Minimal Tripo v3 API client.

Docs: https://developers.tripo3d.ai/en/docs
Flow: upload crop -> image-to-model task -> poll -> download GLB.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from simscrub.errors import NoCredits, ReconRejected, ReconTimeout, SimScrubError

BASE = "https://openapi.tripo3d.ai/v3"
ROOT = Path(__file__).resolve().parents[2]

# Cheapest textured image-to-3D tier ("Standard Texture"). The base-colour
# texture keeps the real tape and labels. PBR maps are skipped: SimReady sets
# cardboard roughness itself, and the pricing page doesn't say PBR is free.
SETTINGS = {
    "model": "v3.1-20260211",
    "texture": True,
    "pbr": False,
    "texture_quality": "standard",
}
CREDITS_PER_MODEL = 30  # image-to-3D, standard texture (developers.tripo3d.ai/en/pricing)

TERMINAL_FAIL = {"failed", "cancelled", "banned", "expired"}


class MissingKey(SimScrubError):
    chip = "No Tripo API key found. Add TRIPO_API_KEY to the .env file."


class AuthFailed(SimScrubError):
    chip = "Tripo rejected the API key. Check TRIPO_API_KEY in .env."


def load_key() -> str:
    key = os.environ.get("TRIPO_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "TRIPO_API_KEY":
                key = value.strip().strip('"').strip("'")
    if not key:
        raise MissingKey("TRIPO_API_KEY not set")
    return key


class Tripo:
    def __init__(self, key: str | None = None, timeout: float = 30.0):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {key or load_key()}"
        self.timeout = timeout

    def _check(self, r: requests.Response) -> dict:
        if r.status_code in (401, 403):
            raise AuthFailed(f"HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            raise SimScrubError(f"HTTP {r.status_code}: {r.text[:200]}") from None
        if r.status_code == 402 or "credit" in str(body.get("message", "")).lower():
            raise NoCredits(str(body))
        if r.status_code >= 400 or body.get("code", 0) != 0:
            raise ReconRejected(f"HTTP {r.status_code}: {body}")
        return body["data"]

    def balance(self) -> float:
        d = self._check(self.s.get(f"{BASE}/account/balance", timeout=self.timeout))
        return float(d["balance"])

    def upload(self, image: Path) -> str:
        with image.open("rb") as f:
            files = {"file": (image.name, f, "image/png")}
            d = self._check(self.s.post(f"{BASE}/files", files=files, timeout=120))
        return d["file_token"]

    def image_to_model(self, file_token: str, **opts) -> str:
        body = {"input": file_token, **SETTINGS, **opts}
        d = self._check(self.s.post(f"{BASE}/generation/image-to-model", json=body, timeout=self.timeout))
        return d["task_id"]

    def task(self, task_id: str) -> dict:
        return self._check(self.s.get(f"{BASE}/tasks/{task_id}", timeout=self.timeout))

    def wait(self, task_id: str, on_progress=None, interval: float = 3.0, limit: float = 600.0) -> dict:
        t0 = time.time()
        while True:
            d = self.task(task_id)
            status = d.get("status")
            if on_progress:
                on_progress(status, d.get("progress") or 0)
            if status == "success":
                return d
            if status in TERMINAL_FAIL:
                raise ReconRejected(f"{status}: {d.get('error_code')} {d.get('error_message')}")
            if time.time() - t0 > limit:
                raise ReconTimeout(f"task {task_id} still {status} after {limit:.0f}s")
            time.sleep(interval)

    def download(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self.s.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            tmp = dest.with_suffix(".part")
            with tmp.open("wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
            tmp.replace(dest)
        return dest
