"""Turn exceptions into sentences an artist can act on.

The UI shows these as chips. Full tracebacks go to the log drawer only.
"""

from __future__ import annotations


class SimScrubError(Exception):
    """Base error carrying a plain-language chip."""

    chip = "Something went wrong. Details are in the log."

    def __init__(self, detail: str = "", chip: str | None = None):
        super().__init__(detail or self.chip)
        if chip:
            self.chip = chip


class ReconTimeout(SimScrubError):
    chip = "Generator took too long. Swapped in a proxy box."


class ReconRejected(SimScrubError):
    chip = "Generator couldn't read this photo. Swapped in a proxy box."


class MeshUnusable(SimScrubError):
    chip = "Mesh came back too broken to fix. Swapped in a proxy box."


class NoCredits(SimScrubError):
    chip = "Out of Tripo credits. Using proxy boxes until topped up."


class VideoUnreadable(SimScrubError):
    chip = "Couldn't read that video. Try an MP4 or MOV from your phone."


def chip_for(exc: BaseException) -> str:
    if isinstance(exc, SimScrubError):
        return exc.chip
    try:
        import requests
        if isinstance(exc, requests.Timeout):
            return ReconTimeout.chip
        if isinstance(exc, requests.ConnectionError):
            return "Lost the network connection. Swapped in a proxy box."
    except ImportError:
        pass
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return ReconTimeout.chip
    if "connection" in name or "network" in name:
        return "Lost the network connection. Swapped in a proxy box."
    return SimScrubError.chip
