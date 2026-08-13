#!/usr/bin/env python3
"""Small deterministic regression tests for the diffuse recovery model."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validate_diffuse_recovery import (  # noqa: E402
    exponential_template,
    fit_template_amplitude,
    total_flux_njy,
    wilson_interval,
)


def main() -> None:
    template, enclosed = exponential_template(6.0, 0.4, 2.0)
    if not math.isclose(float(template.sum()), 1.0, rel_tol=1e-12):
        raise SystemExit("diffuse template does not preserve total injected flux")
    if not 0.95 < enclosed <= 1.01:
        raise SystemExit(f"unexpected finite-template enclosed fraction {enclosed}")
    shape = (301, 301)
    image = np.zeros(shape, dtype=np.float64)
    variance = np.ones(shape, dtype=np.float64) * 4.0
    valid = np.ones(shape, dtype=bool)
    injected = total_flux_njy(24.0, 6.0)
    recovered, uncertainty = fit_template_amplitude(
        image, variance, valid, template, shape[1] // 2, shape[0] // 2, injected
    )
    if not math.isclose(recovered, injected, rel_tol=1e-10):
        raise SystemExit(f"injected flux was not recovered: {recovered} != {injected}")
    if not uncertainty > 0:
        raise SystemExit("formal recovery uncertainty must be positive")
    low, high = wilson_interval(58, 64)
    if not low < 58 / 64 < high:
        raise SystemExit("Wilson interval does not contain the measured completeness")
    print(f"Diffuse recovery regression passed: flux error {(recovered / injected - 1):.3e}")


if __name__ == "__main__":
    main()
