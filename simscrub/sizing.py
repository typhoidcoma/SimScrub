"""Best-guess real size for a box when nobody tape-measured it.

Tripo meshes are unitless: the proportions are trustworthy, the scale is not.
So each box's length : width : height ratio is matched against common stock
shipping-carton sizes (regular slotted cartons, inches), and the closest one
sets the scale. A tape measurement, when given, always wins.

Proportions alone can't tell an 8x6x4 from a 16x12x8, so a gentle prior
favours mid-size parcels. If some boxes in the job *were* measured, that prior
centres on them instead.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np

IN = 0.0254

# Common stock carton sizes (L x W x H, inches).
CATALOG: list[tuple[int, int, int]] = [
    (6, 4, 4), (8, 6, 4), (9, 6, 3), (10, 8, 4), (10, 8, 6), (12, 9, 3), (12, 9, 6),
    (12, 10, 8), (12, 12, 6), (12, 12, 12), (14, 10, 4), (14, 10, 6), (14, 14, 8),
    (16, 12, 4), (16, 12, 8), (16, 12, 12), (18, 12, 6), (18, 14, 8), (18, 18, 12),
    (20, 14, 10), (20, 20, 12), (24, 18, 12),
]
TYPICAL_LONG_IN = 14.0   # prior centre when nothing was measured
PRIOR_WEIGHT = 0.12      # how strongly size is pulled toward the centre


@dataclass
class Guess:
    long_m: float            # target length of the mesh's long side, meters
    label: str               # e.g. "16 × 12 × 8 in"
    ratio_error: float       # 0 = identical proportions


def guess_size(extents: np.ndarray, centre_long_m: float | None = None) -> Guess:
    """``extents`` = (x long, y short, z height) of the oriented mesh, any units."""
    x, y, z = (float(v) for v in extents)
    ours = np.log([y / x, z / x])
    centre = (centre_long_m / IN) if centre_long_m else TYPICAL_LONG_IN
    best = None
    for dims in CATALOG:
        # The box may sit on any face: try every assignment of its sides to x/y/z.
        for a, b, c in set(itertools.permutations(dims)):
            if b > a:
                continue  # x is the longer horizontal side by construction
            err = float(np.abs(ours - np.log([b / a, c / a])).sum())
            score = err + PRIOR_WEIGHT * abs(math.log(a / centre))
            if best is None or score < best[0]:
                best = (score, a, err, dims)
    _, a, err, dims = best
    return Guess(long_m=a * IN, label=f"{dims[0]} × {dims[1]} × {dims[2]} in", ratio_error=err)
