# Step-By-Step Comparison Guide

This guide shows how to compare deterministic and AI downscaling using the same input files, same command-line options, and matching output products.

## 1. Prepare The Environment

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create an output folder:

```bash
mkdir -p output/comparison
```

## 2. Inspect Inputs

Inspect the target grid:

```bash
python downscale_pollutant.py --inspect-geodat data/geo.dat
```

Inspect CALMET records:

```bash
python downscale_pollutant.py --inspect-calmet data/cmet.dat
```

Inspect stations and background estimate:

```bash
python downscale_pollutant.py \
  --pollutant NO2 \
  --inspect-groundtruth data/groundtruth.csv
```

Expected local comparison inputs:

```text
data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif
data/cmet.dat
data/geo.dat
data/groundtruth.csv
```

## 3. Run Deterministic Downscaling

```bash
python downscale_pollutant.py \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/comparison/deterministic_no2.tif \
  --pollutant NO2 \
  --input-band 1 \
  --groundtruth-csv data/groundtruth.csv \
  --groundtruth-value-column NO2 \
  --validate \
  --station-report output/comparison/deterministic_station_report.json \
  --write-weight output/comparison/deterministic_weight.tif \
  --write-correction output/comparison/deterministic_correction.tif
```

## 4. Run AI Downscaling

Use the same input files and the same flags. Change only `--method` and output paths:

```bash
python downscale_pollutant.py --method ai \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/comparison/ai_no2.tif \
  --pollutant NO2 \
  --input-band 1 \
  --groundtruth-csv data/groundtruth.csv \
  --groundtruth-value-column NO2 \
  --validate \
  --station-report output/comparison/ai_station_report.json \
  --write-weight output/comparison/ai_weight.tif \
  --write-correction output/comparison/ai_correction.tif
```

## 5. Compare Console Validation

For each run, note the `Conservation validation` block.

Compare:

- `conservative_allocation.bias`
- `conservative_allocation.mae`
- `conservative_allocation.rmse`
- `written_regularized_output.bias`
- `written_regularized_output.mae`
- `written_regularized_output.rmse`

The conservative allocation block should usually show smaller errors than the written regularized output because seamless/deblocking modifies the final raster.

## 6. Compare Station Reports

Open:

```text
output/comparison/deterministic_station_report.json
output/comparison/ai_station_report.json
```

Compare these sections:

- `station_metrics_before_correction`
- `station_metrics_after_correction_conservative`
- `station_metrics_after_correction_regularized`
- `station_correction`
- `conservation_validation`

Key metrics:

- `bias_pred_minus_obs`: signed station bias.
- `mae`: mean absolute station error.
- `rmse`: root mean square station error.
- `corr`: station correlation when enough station variance exists.

## 7. Compare Rasters In GIS

Load these GeoTIFFs in QGIS, ArcGIS, or another raster viewer:

```text
output/comparison/deterministic_no2.tif
output/comparison/ai_no2.tif
output/comparison/deterministic_weight.tif
output/comparison/ai_weight.tif
output/comparison/deterministic_correction.tif
output/comparison/ai_correction.tif
```

Use the same color ramp, same min/max range, and same nodata treatment for both methods.

Recommended visual checks:

- Does either method create artificial hotspots?
- Are coarse source-pixel seams visible?
- Do station correction fields differ strongly?
- Are high-weight areas physically plausible relative to terrain, land-use, and meteorology?
- Does the AI output preserve broad source-raster patterns while changing fine-grid allocation?

## 8. Create Difference Rasters

Use Python to create AI-minus-deterministic rasters:

```bash
python - <<'PY'
from pathlib import Path
import numpy as np
import rasterio

pairs = [
    ("ai_no2.tif", "deterministic_no2.tif", "diff_ai_minus_deterministic_no2.tif"),
    ("ai_weight.tif", "deterministic_weight.tif", "diff_ai_minus_deterministic_weight.tif"),
    ("ai_correction.tif", "deterministic_correction.tif", "diff_ai_minus_deterministic_correction.tif"),
]

base = Path("output/comparison")
for ai_name, det_name, out_name in pairs:
    with rasterio.open(base / ai_name) as ai, rasterio.open(base / det_name) as det:
        a = ai.read(1).astype("float32")
        d = det.read(1).astype("float32")
        diff = a - d
        profile = ai.profile.copy()
        profile.update(dtype="float32", count=1, nodata=np.nan)
        with rasterio.open(base / out_name, "w", **profile) as dst:
            dst.write(diff.astype("float32"), 1)
print("Wrote difference rasters in output/comparison")
PY
```

Load the difference rasters and use a diverging color ramp centered on zero.

## 9. Optional Strict-Conservation Comparison

To compare only the conservative allocation stage without visual smoothing, run both methods with:

```bash
--no-seamless --deblock-sigma-m 0
```

Example deterministic strict run:

```bash
python downscale_pollutant.py \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/comparison/deterministic_no2_strict.tif \
  --pollutant NO2 \
  --input-band 1 \
  --groundtruth-csv data/groundtruth.csv \
  --groundtruth-value-column NO2 \
  --validate \
  --no-seamless \
  --deblock-sigma-m 0 \
  --station-report output/comparison/deterministic_strict_station_report.json \
  --write-weight output/comparison/deterministic_strict_weight.tif
```

Example AI strict run:

```bash
python downscale_pollutant.py --method ai \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/comparison/ai_no2_strict.tif \
  --pollutant NO2 \
  --input-band 1 \
  --groundtruth-csv data/groundtruth.csv \
  --groundtruth-value-column NO2 \
  --validate \
  --no-seamless \
  --deblock-sigma-m 0 \
  --station-report output/comparison/ai_strict_station_report.json \
  --write-weight output/comparison/ai_strict_weight.tif
```

## 10. Suggested Comparison Table

Create a table with one row per method:

```text
method
station_mae_before
station_rmse_before
station_mae_after_conservative
station_rmse_after_conservative
station_mae_after_regularized
station_rmse_after_regularized
conservation_mae_conservative
conservation_rmse_conservative
conservation_mae_written
conservation_rmse_written
visual_notes
```

This keeps the comparison balanced: station fit, conservation behavior, and visual plausibility are all visible.

## 11. Interpretation Checklist

Use the deterministic method as a transparent baseline. Use the AI method as a second model family for sensitivity analysis.

Prefer a method only when it performs better across several criteria:

- lower station error without creating unrealistic spatial artifacts;
- acceptable conservation validation;
- plausible relationship to terrain, land-use, and meteorology;
- stable behavior when seamless/deblocking settings are changed;
- stable behavior across more than one date or pollutant field.
