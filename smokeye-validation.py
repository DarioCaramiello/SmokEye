#!/usr/bin/env python3
"""
SmokEye validation entry point.

This script validates CALPUFF arbitrary-unit outputs against air-quality
stations and SmokEye-downscaled satellite/reference rasters. It is designed as
an additive entry point for the SmokEye repository: it does not replace
`downscale_pollutant.py`, `prepare_calpuff.py`, or `compare_calpuff_satellite.py`.

Typical use:

  python smokeye-validation.py \
    --pollutant NO2 \
    --calpuff-raw output/no2_raw_model_units.tif \
    --satellite output/no2_downscaled_satellite.tif \
    --stations data/stations_no2.csv \
    --station-id-column station_id \
    --x-column lon --y-column lat \
    --observed-column NO2 \
    --out-prefix output/validation/no2

Station CSV must contain at least station id, x, y, and observed value columns.
If `--calpuff-at-station-column` or `--satellite-at-station-column` are absent,
the script samples the corresponding rasters at station coordinates.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import numpy as np
except Exception as exc:  # pragma: no cover
    raise SystemExit("smokeye-validation.py requires numpy") from exc

try:
    import rasterio
except Exception as exc:  # pragma: no cover
    raise SystemExit("smokeye-validation.py requires rasterio") from exc


@dataclass
class Metrics:
    n: int
    mean_obs: Optional[float]
    mean_pred: Optional[float]
    bias_pred_minus_obs: Optional[float]
    mae: Optional[float]
    rmse: Optional[float]
    corr: Optional[float]
    slope_through_origin: Optional[float]
    median_abs_error: Optional[float]


@dataclass
class Calibration:
    pollutant: str
    scale: float
    offset: float
    background: float
    formula: str
    train_n: int
    method: str


@dataclass
class ValidationReport:
    pollutant: str
    target_unit: str
    station_count: int
    train_station_count: int
    test_station_count: int
    calibration: Calibration
    station_metrics_train: Metrics
    station_metrics_test: Metrics
    satellite_station_metrics: Optional[Metrics]
    spatial_metrics: Dict[str, Any]
    hotspot_metrics: Dict[str, Any]
    inputs: Dict[str, Any]
    caveats: List[str]


def finite_pair_arrays(obs: Sequence[float], pred: Sequence[float]) -> Tuple[np.ndarray, np.ndarray]:
    o = np.asarray(obs, dtype="float64")
    p = np.asarray(pred, dtype="float64")
    mask = np.isfinite(o) & np.isfinite(p)
    return o[mask], p[mask]


def compute_metrics(obs: Sequence[float], pred: Sequence[float]) -> Metrics:
    o, p = finite_pair_arrays(obs, pred)
    n = int(o.size)
    if n == 0:
        return Metrics(0, None, None, None, None, None, None, None, None)
    diff = p - o
    corr = None
    if n >= 2 and float(np.std(o)) > 0.0 and float(np.std(p)) > 0.0:
        corr = float(np.corrcoef(o, p)[0, 1])
    denom = float(np.sum(o * o))
    slope = float(np.sum(o * p) / denom) if denom > 0 else None
    return Metrics(
        n=n,
        mean_obs=float(np.mean(o)),
        mean_pred=float(np.mean(p)),
        bias_pred_minus_obs=float(np.mean(diff)),
        mae=float(np.mean(np.abs(diff))),
        rmse=float(math.sqrt(np.mean(diff * diff))),
        corr=corr,
        slope_through_origin=slope,
        median_abs_error=float(np.median(np.abs(diff))),
    )


def read_station_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        raise ValueError(f"Station CSV has no rows: {path}")
    return rows


def parse_float(value: Any, field: str) -> float:
    try:
        if value is None or value == "":
            return float("nan")
        return float(value)
    except Exception as exc:
        raise ValueError(f"Cannot parse numeric field {field!r}: {value!r}") from exc


def sample_raster_at_points(path: Path, points: Sequence[Tuple[float, float]], band: int = 1) -> List[float]:
    values: List[float] = []
    with rasterio.open(path) as ds:
        for sample in ds.sample(points, indexes=band):
            v = float(sample[0])
            if ds.nodata is not None and v == ds.nodata:
                v = float("nan")
            values.append(v)
    return values


def fit_linear_calibration(raw: Sequence[float], obs: Sequence[float], *, fit_offset: bool, background: float) -> Tuple[float, float]:
    x, y = finite_pair_arrays(raw, obs)
    y = y - float(background)
    if x.size < (2 if fit_offset else 1):
        raise ValueError("Not enough finite station pairs to fit calibration")
    if fit_offset:
        a = np.vstack([x, np.ones_like(x)]).T
        scale, offset = np.linalg.lstsq(a, y, rcond=None)[0]
        return float(scale), float(offset)
    denom = float(np.sum(x * x))
    if denom <= 0.0:
        raise ValueError("Cannot fit through-origin scale because CALPUFF station values are all zero")
    scale = float(np.sum(x * y) / denom)
    return scale, 0.0


def split_train_test(
    rows: List[Dict[str, Any]],
    station_id_column: str,
    test_fraction: float,
    seed: int,
    explicit_test_ids: Optional[set[str]],
) -> Tuple[List[int], List[int]]:
    station_ids = sorted({str(r[station_id_column]) for r in rows})
    if explicit_test_ids:
        test_ids = set(explicit_test_ids)
    else:
        rng = random.Random(seed)
        shuffled = station_ids[:]
        rng.shuffle(shuffled)
        n_test = max(1, int(round(len(shuffled) * test_fraction))) if len(shuffled) > 1 else 0
        test_ids = set(shuffled[:n_test])
    train_idx, test_idx = [], []
    for idx, row in enumerate(rows):
        sid = str(row[station_id_column])
        if sid in test_ids:
            test_idx.append(idx)
        else:
            train_idx.append(idx)
    if not train_idx:
        raise ValueError("Training split is empty; reduce --test-fraction or revise --test-station-ids")
    return train_idx, test_idx


def take(values: Sequence[float], idx: Sequence[int]) -> List[float]:
    return [values[i] for i in idx]


def raster_values(path: Path, band: int = 1) -> Tuple[np.ndarray, Dict[str, Any]]:
    with rasterio.open(path) as ds:
        arr = ds.read(band).astype("float64")
        profile = ds.profile.copy()
        nodata = ds.nodata
    if nodata is not None:
        arr[arr == nodata] = np.nan
    return arr, profile


def spatial_comparison(model_path: Path, reference_path: Path, model_band: int, ref_band: int) -> Dict[str, Any]:
    model, model_profile = raster_values(model_path, model_band)
    ref, ref_profile = raster_values(reference_path, ref_band)
    if model.shape != ref.shape:
        raise ValueError(f"Raster shapes differ: model {model.shape}, reference {ref.shape}")
    mask = np.isfinite(model) & np.isfinite(ref)
    if int(mask.sum()) == 0:
        return {"n": 0, "error": "No overlapping finite raster cells"}
    return {
        "n": int(mask.sum()),
        "model_mean": float(np.mean(model[mask])),
        "reference_mean": float(np.mean(ref[mask])),
        "bias_model_minus_reference": float(np.mean(model[mask] - ref[mask])),
        "mae": float(np.mean(np.abs(model[mask] - ref[mask]))),
        "rmse": float(math.sqrt(np.mean((model[mask] - ref[mask]) ** 2))),
        "corr": float(np.corrcoef(model[mask], ref[mask])[0, 1]) if np.std(model[mask]) > 0 and np.std(ref[mask]) > 0 else None,
        "mean_ratio_model_over_reference": float(np.mean(model[mask] / ref[mask])) if np.all(ref[mask] != 0) else None,
        "crs_model": str(model_profile.get("crs")),
        "crs_reference": str(ref_profile.get("crs")),
        "transform_model": str(model_profile.get("transform")),
        "transform_reference": str(ref_profile.get("transform")),
    }


def hotspot_comparison(model_path: Path, reference_path: Path, percentile: float, model_band: int, ref_band: int) -> Dict[str, Any]:
    model, _ = raster_values(model_path, model_band)
    ref, _ = raster_values(reference_path, ref_band)
    if model.shape != ref.shape:
        raise ValueError(f"Raster shapes differ: model {model.shape}, reference {ref.shape}")
    mask = np.isfinite(model) & np.isfinite(ref)
    if int(mask.sum()) == 0:
        return {"n": 0, "error": "No overlapping finite raster cells"}
    model_threshold = float(np.nanpercentile(model[mask], percentile))
    ref_threshold = float(np.nanpercentile(ref[mask], percentile))
    m_hot = (model >= model_threshold) & mask
    r_hot = (ref >= ref_threshold) & mask
    tp = int(np.sum(m_hot & r_hot))
    fp = int(np.sum(m_hot & ~r_hot))
    fn = int(np.sum(~m_hot & r_hot))
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = 2 * precision * recall / (precision + recall) if precision is not None and recall is not None and (precision + recall) else None
    return {
        "percentile": percentile,
        "model_threshold": model_threshold,
        "reference_threshold": ref_threshold,
        "true_positive_cells": tp,
        "false_positive_cells": fp,
        "false_negative_cells": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def write_station_csv(
    path: Path,
    rows: Sequence[Dict[str, str]],
    station_ids: Sequence[str],
    obs: Sequence[float],
    raw: Sequence[float],
    calibrated: Sequence[float],
    satellite: Optional[Sequence[float]],
    split: Sequence[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["station_id", "split", "observed", "calpuff_raw", "calpuff_calibrated"]
    if satellite is not None:
        fields.append("satellite_reference")
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i in range(len(rows)):
            out = {
                "station_id": station_ids[i],
                "split": split[i],
                "observed": obs[i],
                "calpuff_raw": raw[i],
                "calpuff_calibrated": calibrated[i],
            }
            if satellite is not None:
                out["satellite_reference"] = satellite[i]
            writer.writerow(out)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate CALPUFF arbitrary-unit rasters against stations and SmokEye-downscaled satellite rasters.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--pollutant", required=True, help="Pollutant label, e.g. NO2, CO, PM10")
    parser.add_argument("--target-unit", default="ug_m3", help="Final target concentration unit")
    parser.add_argument("--calpuff-raw", required=True, type=Path, help="CALPUFF raster in arbitrary/model units")
    parser.add_argument("--satellite", type=Path, help="SmokEye-downscaled satellite/reference raster in target units")
    parser.add_argument("--stations", required=True, type=Path, help="Station CSV with observations and coordinates")
    parser.add_argument("--out-prefix", required=True, type=Path, help="Output prefix for JSON and CSV reports")
    parser.add_argument("--station-id-column", default="station_id")
    parser.add_argument("--x-column", default="lon", help="Station x coordinate column in raster CRS")
    parser.add_argument("--y-column", default="lat", help="Station y coordinate column in raster CRS")
    parser.add_argument("--observed-column", required=True, help="Observed station concentration column")
    parser.add_argument("--calpuff-at-station-column", help="Optional pre-sampled CALPUFF station column")
    parser.add_argument("--satellite-at-station-column", help="Optional pre-sampled satellite station column")
    parser.add_argument("--calpuff-band", type=int, default=1)
    parser.add_argument("--satellite-band", type=int, default=1)
    parser.add_argument("--test-fraction", type=float, default=0.30, help="Fraction of station IDs reserved for independent validation")
    parser.add_argument("--test-station-ids", help="Comma-separated station IDs for the independent test split")
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--fit-offset", action="store_true", help="Fit CALPUFF offset in addition to scale")
    parser.add_argument("--background", type=float, default=0.0, help="Known/assumed background concentration in target unit")
    parser.add_argument("--hotspot-percentile", type=float, default=90.0)
    parser.add_argument("--write-calibrated-raster", action="store_true", help="Write calibrated CALPUFF raster")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    rows = read_station_csv(args.stations)
    for col in [args.station_id_column, args.x_column, args.y_column, args.observed_column]:
        if col not in rows[0]:
            raise ValueError(f"Missing required station CSV column: {col}")

    station_ids = [str(r[args.station_id_column]) for r in rows]
    points = [(parse_float(r[args.x_column], args.x_column), parse_float(r[args.y_column], args.y_column)) for r in rows]
    obs = [parse_float(r[args.observed_column], args.observed_column) for r in rows]

    if args.calpuff_at_station_column:
        raw = [parse_float(r[args.calpuff_at_station_column], args.calpuff_at_station_column) for r in rows]
    else:
        raw = sample_raster_at_points(args.calpuff_raw, points, args.calpuff_band)

    satellite_values: Optional[List[float]] = None
    if args.satellite_at_station_column:
        satellite_values = [parse_float(r[args.satellite_at_station_column], args.satellite_at_station_column) for r in rows]
    elif args.satellite:
        satellite_values = sample_raster_at_points(args.satellite, points, args.satellite_band)

    explicit_test_ids = set(args.test_station_ids.split(",")) if args.test_station_ids else None
    train_idx, test_idx = split_train_test(rows, args.station_id_column, args.test_fraction, args.random_seed, explicit_test_ids)

    scale, offset = fit_linear_calibration(take(raw, train_idx), take(obs, train_idx), fit_offset=args.fit_offset, background=args.background)
    calibrated = [float(scale) * v + float(offset) + float(args.background) if math.isfinite(v) else float("nan") for v in raw]

    split = ["test" if i in set(test_idx) else "train" for i in range(len(rows))]
    station_csv_path = args.out_prefix.with_suffix(".stations.csv")
    write_station_csv(station_csv_path, rows, station_ids, obs, raw, calibrated, satellite_values, split)

    calibrated_raster_path = None
    if args.write_calibrated_raster:
        calibrated_raster_path = args.out_prefix.with_suffix(".calpuff_calibrated.tif")
        with rasterio.open(args.calpuff_raw) as src:
            arr = src.read(args.calpuff_band).astype("float32")
            profile = src.profile.copy()
            nodata = src.nodata
            out = arr.astype("float64")
            if nodata is not None:
                out[arr == nodata] = np.nan
            out = out * scale + offset + args.background
            profile.update(dtype="float32", count=1, compress="deflate", nodata=np.nan)
            calibrated_raster_path.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(calibrated_raster_path, "w", **profile) as dst:
                dst.write(out.astype("float32"), 1)

    spatial = {}
    hotspots = {}
    spatial_model_path = calibrated_raster_path or args.calpuff_raw
    if args.satellite:
        if calibrated_raster_path is None:
            # Spatial comparison with raw CALPUFF would be physically misleading.
            # Create an in-memory temporary calibrated raster only if requested by user.
            spatial = {"status": "skipped", "reason": "Use --write-calibrated-raster to compare calibrated CALPUFF raster against satellite raster."}
            hotspots = {"status": "skipped", "reason": "Use --write-calibrated-raster for hotspot raster comparison."}
        else:
            spatial = spatial_comparison(spatial_model_path, args.satellite, 1, args.satellite_band)
            hotspots = hotspot_comparison(spatial_model_path, args.satellite, args.hotspot_percentile, 1, args.satellite_band)

    calibration = Calibration(
        pollutant=args.pollutant,
        scale=scale,
        offset=offset,
        background=args.background,
        formula="calibrated_CALPUFF = raw_CALPUFF * scale + offset + background",
        train_n=len(train_idx),
        method="ordinary_least_squares_with_offset" if args.fit_offset else "least_squares_scale_through_origin",
    )

    report = ValidationReport(
        pollutant=args.pollutant,
        target_unit=args.target_unit,
        station_count=len(rows),
        train_station_count=len({station_ids[i] for i in train_idx}),
        test_station_count=len({station_ids[i] for i in test_idx}),
        calibration=calibration,
        station_metrics_train=compute_metrics(take(obs, train_idx), take(calibrated, train_idx)),
        station_metrics_test=compute_metrics(take(obs, test_idx), take(calibrated, test_idx)),
        satellite_station_metrics=compute_metrics(obs, satellite_values) if satellite_values is not None else None,
        spatial_metrics=spatial,
        hotspot_metrics=hotspots,
        inputs={
            "calpuff_raw": str(args.calpuff_raw),
            "satellite": str(args.satellite) if args.satellite else None,
            "stations": str(args.stations),
            "station_csv": str(station_csv_path),
            "calibrated_raster": str(calibrated_raster_path) if calibrated_raster_path else None,
        },
        caveats=[
            "CALPUFF arbitrary units are not physically interpretable until calibrated against independent observations.",
            "Do not fit scale/background with the same stations or dates used as final evidence of performance.",
            "Satellite rasters may represent column or model-assisted downscaled products, while stations are near-surface observations.",
            "Spatial raster agreement is diagnostic and must be interpreted with time, unit, chemistry, and vertical-representativeness assumptions.",
        ],
    )
    report_path = args.out_prefix.with_suffix(".validation.json")
    write_json(report_path, asdict(report))
    print(f"Wrote {report_path}")
    print(f"Wrote {station_csv_path}")
    if calibrated_raster_path:
        print(f"Wrote {calibrated_raster_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
