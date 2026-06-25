"""AI weight strategy for the shared SmokEye downscaling workflow."""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
from scipy.ndimage import gaussian_filter

from smokeye import downscaler


def _finite_fill(arr: np.ndarray, fallback: float = 0.0) -> np.ndarray:
    a = np.asarray(arr, dtype=float)
    if np.isfinite(a).any():
        fill = float(np.nanmedian(a[np.isfinite(a)]))
    else:
        fill = float(fallback)
    return np.where(np.isfinite(a), a, fill)


def _standardize_feature(arr: np.ndarray) -> np.ndarray:
    scaled = downscaler.robust01(_finite_fill(arr))
    return (scaled - 0.5) * 2.0


def _feature_stack(
    grid: downscaler.GeoGrid,
    met: Dict[str, np.ndarray],
) -> Tuple[np.ndarray, List[str]]:
    rows = np.linspace(-1.0, 1.0, grid.ny, dtype=float)[:, None]
    cols = np.linspace(-1.0, 1.0, grid.nx, dtype=float)[None, :]
    yy = np.repeat(rows, grid.nx, axis=1)
    xx = np.repeat(cols, grid.ny, axis=0)

    features: List[np.ndarray] = [xx, yy, xx * yy, xx * xx, yy * yy]
    names = ["x", "y", "xy", "x2", "y2"]

    elev = grid.elevation if grid.elevation is not None else met.get("elevation_calmet")
    if elev is not None:
        features.append(_standardize_feature(elev))
        names.append("elevation")

    if grid.landuse is not None:
        lu = _finite_fill(grid.landuse)
        features.append(_standardize_feature(lu))
        names.append("landuse_code")
        for cls in sorted(int(v) for v in np.unique(lu[np.isfinite(lu)]))[:24]:
            features.append((lu == cls).astype(float))
            names.append(f"landuse_{cls}")

    for key in sorted(met):
        if key == "elevation_calmet" and elev is not None:
            continue
        arr = met[key]
        if arr.shape == (grid.ny, grid.nx):
            features.append(_standardize_feature(arr))
            names.append(key)

    cube = np.stack(features, axis=-1)
    return cube.reshape((-1, cube.shape[-1])), names


def _ridge_fit(hidden: np.ndarray, target: np.ndarray, alpha: float) -> np.ndarray:
    lhs = hidden.T @ hidden
    lhs.flat[:: lhs.shape[0] + 1] += alpha
    rhs = hidden.T @ target
    return np.linalg.solve(lhs, rhs)


def build_ai_weights(
    grid: downscaler.GeoGrid,
    met: Dict[str, np.ndarray],
    min_weight: float = 0.05,
) -> np.ndarray:
    """Build a positive fine-grid weight field with a deterministic ML model."""
    x, feature_names = _feature_stack(grid, met)
    teacher = downscaler.build_weights(grid, met, min_weight=min_weight).reshape(-1)
    valid = np.isfinite(teacher) & (teacher > 0) & np.all(np.isfinite(x), axis=1)
    if valid.sum() < max(10, x.shape[1] + 2):
        return np.maximum(np.ones((grid.ny, grid.nx), dtype=float), min_weight)

    rng = np.random.default_rng(42)
    hidden_width = min(96, max(24, x.shape[1] * 4))
    w_in = rng.normal(0.0, 0.85, size=(x.shape[1], hidden_width))
    b_in = rng.normal(0.0, 0.35, size=(hidden_width,))
    hidden = np.tanh(x @ w_in + b_in)
    hidden = np.concatenate([np.ones((hidden.shape[0], 1)), x, hidden], axis=1)

    y = np.log(np.clip(teacher, min_weight, None))
    coef = _ridge_fit(hidden[valid], y[valid], alpha=1.0e-2)
    pred = np.exp(hidden @ coef).reshape((grid.ny, grid.nx))

    sigma_cells = max(0.5, min(3.0, 600.0 / float(max(grid.dx, grid.dy))))
    smooth = gaussian_filter(pred, sigma=sigma_cells, mode="nearest")
    learned = 0.75 * pred + 0.25 * smooth
    learned = np.where(np.isfinite(learned), learned, np.nanmedian(teacher))
    learned = np.maximum(learned, min_weight)

    median = float(np.nanmedian(learned))
    if np.isfinite(median) and median > 0:
        learned = learned / median
    learned = np.clip(learned, min_weight, 20.0)

    print(
        "AI weight model:",
        f"features={len(feature_names)}",
        f"hidden={hidden_width}",
        f"training_cells={int(valid.sum())}",
    )
    return learned


def ai_raster_tags(tags: dict) -> dict:
    out = dict(tags)
    out.update(
        {
            "method": "ai_conservative_dynamic_downscaling",
            "ai_model": "deterministic_extreme_learning_machine_ridge",
        }
    )
    return out


def main() -> None:
    downscaler.main(
        weight_builder=build_ai_weights,
        raster_tag_builder=ai_raster_tags,
        method_name="ai",
    )


if __name__ == "__main__":
    main()
