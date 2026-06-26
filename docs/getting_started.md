# Getting Started

This guide introduces a complete SmokEye workflow, from installation to reproducible example runs. It is written for a user who wants to understand not only which commands to execute, but also what each command produces and how to interpret the quality-control outputs.

SmokEye downscales a gridded pollutant raster to a CALMET `GEO.DAT` grid. The command supports two weight-building methods:

- deterministic: explicit terrain, land-use, and meteorological rules;
- AI: a deterministic machine-learning weight strategy with the same input and output contract.

Both methods use the same readers, conservative allocation engine, station correction workflow, validation logic, deblocking logic, and raster writers.

## 1. Prepare The Software Environment

Start from the repository root:

```bash
cd /path/to/SmokEye
```

Create and activate a Python virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If `rasterio` or other geospatial packages cannot be installed cleanly with `pip`, use a conda-forge environment instead:

```bash
conda create -n smokeye -c conda-forge python=3.11 numpy rasterio shapely pyproj scipy
conda activate smokeye
```

Confirm that the command-line interface is available:

```bash
python downscale_pollutant.py --help
```

The help output should include:

```text
--method {deterministic,ai}
--pollutant POLLUTANT
--groundtruth-csv GROUNDTRUTH_CSV
--validate
--no-seamless
--deblock-sigma-m DEBLOCK_SIGMA_M
```

## 2. Organize Input And Output Files

A typical project layout is:

```text
data/
  S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif
  cmet.dat
  geo.dat
  groundtruth.csv
examples/
  groundtruth_example.csv
output/
```

The four positional inputs used for a normal downscaling run are:

```text
input_tif calmet_dat geodat output_tif
```

They mean:

- `input_tif`: pollutant GeoTIFF with a valid CRS and numeric pollutant values;
- `calmet_dat`: CALMET/CMET binary meteorology file, or an `.npz` meteorology file;
- `geodat`: CALMET `GEO.DAT` target grid description;
- `output_tif`: output single-band GeoTIFF aligned to the `GEO.DAT` grid.

Create output folders for the examples:

```bash
mkdir -p output/getting_started/no_groundtruth
mkdir -p output/getting_started/groundtruth
mkdir -p output/getting_started/strict
mkdir -p output/getting_started/metrics
mkdir -p output/getting_started/smokeye_use_case
```

## 3. Inspect The Inputs Before Downscaling

Always inspect the target grid before running a production downscaling job:

```bash
python downscale_pollutant.py --inspect-geodat data/geo.dat
```

This prints JSON metadata for the inferred grid, including CRS, grid dimensions, cell size, bounds, and whether elevation or land-use were found in `GEO.DAT`. Review this output carefully. CRS, transform, dimensions, and grid origin are correctness-critical for conservative spatial allocation.

Also confirm the storage order of embedded arrays. The defaults assume `GEO.DAT` and CALMET binary arrays are stored from the lower/southern row upward:

```bash
--geodat-array-origin lower
--calmet-array-origin lower
```

If terrain, land-use, meteorology, or a written weight raster appears vertically mirrored, rerun diagnostics with `upper` for the affected source. A useful strict diagnostic run writes the weight field without regularization:

```bash
--write-weight output/diagnostic_weight.tif --no-seamless --deblock-sigma-m 0
```

Inspect CALMET records:

```bash
python downscale_pollutant.py --inspect-calmet data/cmet.dat
```

The reader looks for common gridded fields such as:

```text
ZI, TEMPK, USTAR, Z0, U-LEV 1, V-LEV 1, ELEV, ILANDU
```

If the CALMET binary layout is not recognized, export meteorological fields to an `.npz` file on the `GEO.DAT` grid and use:

```bash
--met-npz data/met_fields.npz
```

Supported `.npz` field names include:

```text
pblh, ws10, u10, v10, ustar, tempk, z0, elevation_calmet, landuse_calmet
```

## 4. Understand Time Selection

SmokEye treats each command as one analysis time or one pre-aggregated time window. Downscaling enforces timestamp consistency between the pollutant raster and CALMET/CMET meteorology. It uses explicit `--satellite-time-start/--satellite-time-end` values when supplied, otherwise it reads common GeoTIFF time metadata; it does not infer time from filenames. Before running downscaling, decide what time basis the output should represent, then prepare all inputs consistently:

- choose the pollutant raster band for that time or averaging period;
- select or average CALMET meteorology for the same period;
- prefilter or average station data to the same period before writing the ground-truth CSV;
- use an `.npz` meteorology file only after its arrays have already been time-selected or time-averaged.

The `--satellite-time-start` and `--satellite-time-end` values are ISO datetimes, for example `2024-06-28T11:00:00`, `2024-06-28 11:00:00`, `2024-06-28T11:00:00Z`, or `2024-06-28T13:00:00+02:00`. Timezone-aware values are converted to UTC before comparison and diagnostics. Start and end must be supplied together, and the end must be after the start unless both are the same instant and `--satellite-instant-duration-minutes` is used to expand that instant into a centered validity window.

For CALMET/CMET binary files, the time behavior is controlled by:

```bash
--calmet-selector mean
--calmet-stamp INTEGER
--max-calmet-stamp-delta INTEGER
```

When the pollutant raster has a known time window and `--calmet-stamp` is not supplied, SmokEye derives a target `YYYYMMDDHH` CALMET stamp from the satellite midpoint and chooses the closest available record for each meteorological variable. `--max-calmet-stamp-delta` limits how far the selected CALMET stamp may be from the requested stamp. `--calmet-stamp` overrides the derived stamp. `--calmet-selector mean` computes a cellwise mean over supported records and is useful only when the pollutant raster is itself a temporal average over the same period.

For example, if the CALMET producer documents that stamp `2024062811` corresponds to the intended analysis hour, run:

```bash
python downscale_pollutant.py \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/getting_started/no_groundtruth/deterministic_no2_stamp.tif \
  --pollutant NO2 \
  --input-band 1 \
  --calmet-stamp 2024062811 \
  --validate
```

If the pollutant raster represents a daily or multi-hour average and the CALMET file contains all matching records, use:

```bash
--calmet-selector mean
```

Untimed pollutant rasters fail by default; use `--allow-untimed-satellite` only when the missing timestamp is an explicit, documented assumption. Station CSV rows also have no internal time axis in the current workflow, so temporal consistency is established before the CSV is passed to SmokEye.

## 5. Inspect Ground Truth And Estimate Average Ground Level

Ground-truth station data are optional. A station CSV must contain station ID, latitude, longitude, and a pollutant value column:

```csv
ID,LAT,LON,NO2
AQSTN_A1,40.814289,14.267230,9.9736753e-05
AQSTN_B2,40.845249,14.321457,0.00015246817
```

Inspect the example station file:

```bash
python downscale_pollutant.py \
  --pollutant NO2 \
  --inspect-groundtruth examples/groundtruth_example.csv
```

Inspect a project station file:

```bash
python downscale_pollutant.py \
  --pollutant NO2 \
  --groundtruth-value-column NO2 \
  --inspect-groundtruth data/groundtruth.csv
```

The inspection output includes:

```text
stations.n
stations.value_min
stations.value_max
stations.value_mean
stations.value_median
background_value
```

In SmokEye reports, `background_value` is the estimated average ground-level pollutant background from the station data. By default it is computed as a low-percentile mean, which is intended to reduce the influence of local hot spots:

```bash
--background-mode low-percentile
--background-percentile 40
```

Alternative background estimators are available:

```bash
--background-mode mean
--background-mode median
--background-mode min
--background-mode none
```

Use `mean` or `median` when the station network is spatially representative. Use the default low-percentile mode when stations include localized source-influenced measurements and a background-excess correction is desired.

## 6. Run Deterministic Downscaling Without Ground Truth Correction

This is the simplest full downscaling run. It uses deterministic dynamic weights, writes the main raster, writes the weight raster, and reports conservation metrics:

```bash
python downscale_pollutant.py \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/getting_started/no_groundtruth/deterministic_no2.tif \
  --pollutant NO2 \
  --input-band 1 \
  --validate \
  --write-weight output/getting_started/no_groundtruth/deterministic_weight.tif
```

This run produces:

- `deterministic_no2.tif`: downscaled pollutant raster;
- `deterministic_weight.tif`: final dynamic weight field;
- console validation metrics when `--validate` is present.

The validation block contains:

```text
n
bias
mae
rmse
```

When default deblocking is enabled, validation is reported for two fields:

- `conservative_allocation`: exact conservative allocation before regularization;
- `written_regularized_output`: final written output after seamless/deblocking regularization.

The conservative allocation should generally have lower coarse-scale error because it is evaluated before smoothing.

## 7. Run AI Downscaling Without Ground Truth Correction

Use the same inputs and outputs, changing only `--method` and output paths:

```bash
python downscale_pollutant.py --method ai \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/getting_started/no_groundtruth/ai_no2.tif \
  --pollutant NO2 \
  --input-band 1 \
  --validate \
  --write-weight output/getting_started/no_groundtruth/ai_weight.tif
```

During execution, the AI method prints a short model summary:

```text
AI weight model: features=... hidden=... training_cells=...
```

The AI method is deterministic. Its random hidden layer uses a fixed seed, so repeated runs with the same inputs produce the same weight field.

## 8. Run Deterministic Downscaling With Ground Truth Correction

Ground-truth correction uses station observations to build a smooth multiplicative correction field. The correction modifies the dynamic weights, and the conservative allocation is then rerun.

```bash
python downscale_pollutant.py \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/getting_started/groundtruth/deterministic_no2_corrected.tif \
  --pollutant NO2 \
  --input-band 1 \
  --groundtruth-csv data/groundtruth.csv \
  --groundtruth-value-column NO2 \
  --background-mode low-percentile \
  --background-percentile 40 \
  --validate \
  --station-report output/getting_started/groundtruth/deterministic_station_report.json \
  --write-weight output/getting_started/groundtruth/deterministic_weight_corrected.tif \
  --write-correction output/getting_started/groundtruth/deterministic_correction.tif
```

This run produces:

- corrected pollutant GeoTIFF;
- corrected weight GeoTIFF;
- station correction GeoTIFF;
- station report JSON;
- console conservation validation.

The station report contains:

```text
background_value
station_metrics_before_correction
station_metrics_after_correction_conservative
station_metrics_after_correction_regularized
station_correction
conservation_validation
```

Station metrics include:

```text
n
obs_mean
pred_mean
bias_pred_minus_obs
mae
rmse
corr
```

Interpret these metrics as station agreement diagnostics, not as proof of physical truth. Station measurements are often near-surface observations, while satellite fields may represent column quantities or model-layer values.

## 9. Run AI Downscaling With Ground Truth Correction

The AI run uses the same ground-truth correction workflow:

```bash
python downscale_pollutant.py --method ai \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/getting_started/groundtruth/ai_no2_corrected.tif \
  --pollutant NO2 \
  --input-band 1 \
  --groundtruth-csv data/groundtruth.csv \
  --groundtruth-value-column NO2 \
  --background-mode low-percentile \
  --background-percentile 40 \
  --validate \
  --station-report output/getting_started/groundtruth/ai_station_report.json \
  --write-weight output/getting_started/groundtruth/ai_weight_corrected.tif \
  --write-correction output/getting_started/groundtruth/ai_correction.tif
```

Use the deterministic and AI station reports to compare:

- station error before correction;
- station error after conservative correction;
- station error after final regularization;
- correction-field magnitude and spatial distribution;
- conservation behavior before and after deblocking.

## 10. Run With Default Deblocking

By default, SmokEye applies two regularization steps to reduce visible coarse-pixel boundaries:

```text
--seamless
--deblock-sigma-m 400
```

The default seamless settings are:

```bash
--seamless-baseline-sigma-m 1400
--seamless-anomaly-sigma-m 1000
--seamless-strength 0.95
--seamless-anomaly-min 0.35
--seamless-anomaly-max 2.75
```

The default final deblocking settings are:

```bash
--deblock-sigma-m 400
--deblock-strength 0.75
--deblock-iterations 1
```

Default deblocking improves visual continuity but relaxes strict per-source-pixel conservation in the final written raster. The `--validate` output makes this visible by reporting both exact conservative allocation and written regularized output.

SmokEye use case: deterministic downscaling with conservative allocation diagnostics, default seamless/deblocking regularization, `GEO.DAT` arrays already in upper/north-to-south order, and CALMET arrays in lower/south-to-north order:

```bash
python downscale_pollutant.py \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/getting_started/smokeye_use_case/deterministic_no2_geodat_upper_calmet_lower.tif \
  --pollutant NO2 \
  --input-band 1 \
  --geodat-array-origin upper \
  --calmet-array-origin lower \
  --validate \
  --write-weight output/getting_started/smokeye_use_case/deterministic_weight_geodat_upper_calmet_lower.tif
```

Use this pattern when `GEO.DAT` terrain or land-use diagnostics are already north-to-south in raster row order, while CALMET gridded records still need the default lower-origin flip. Keep `--validate` enabled so the output reports both the exact conservative allocation and the final deblocked raster.

## 11. Run Without Deblocking For Strict Conservation Review

For strict scientific comparison of the conservative allocation stage, disable seamless recomposition and final deblocking:

```bash
python downscale_pollutant.py \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/getting_started/strict/deterministic_no2_strict.tif \
  --pollutant NO2 \
  --input-band 1 \
  --validate \
  --no-seamless \
  --deblock-sigma-m 0 \
  --write-weight output/getting_started/strict/deterministic_weight_strict.tif
```

Strict AI run:

```bash
python downscale_pollutant.py --method ai \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/getting_started/strict/ai_no2_strict.tif \
  --pollutant NO2 \
  --input-band 1 \
  --validate \
  --no-seamless \
  --deblock-sigma-m 0 \
  --write-weight output/getting_started/strict/ai_weight_strict.tif
```

Strict corrected deterministic run:

```bash
python downscale_pollutant.py \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/getting_started/strict/deterministic_no2_corrected_strict.tif \
  --pollutant NO2 \
  --input-band 1 \
  --groundtruth-csv data/groundtruth.csv \
  --groundtruth-value-column NO2 \
  --validate \
  --no-seamless \
  --deblock-sigma-m 0 \
  --station-report output/getting_started/strict/deterministic_strict_station_report.json \
  --write-weight output/getting_started/strict/deterministic_weight_corrected_strict.tif \
  --write-correction output/getting_started/strict/deterministic_correction_strict.tif
```

Strict corrected AI run:

```bash
python downscale_pollutant.py --method ai \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/getting_started/strict/ai_no2_corrected_strict.tif \
  --pollutant NO2 \
  --input-band 1 \
  --groundtruth-csv data/groundtruth.csv \
  --groundtruth-value-column NO2 \
  --validate \
  --no-seamless \
  --deblock-sigma-m 0 \
  --station-report output/getting_started/strict/ai_strict_station_report.json \
  --write-weight output/getting_started/strict/ai_weight_corrected_strict.tif \
  --write-correction output/getting_started/strict/ai_correction_strict.tif
```

The strict runs are useful for regression tests, method comparison, and conservation audits. The default deblocked runs are usually more suitable for visualization.

## 12. Extract Quality Metrics From Station Reports

Station reports are JSON files. The following Python snippet extracts station metrics, conservation metrics, and the estimated average ground-level background value:

```bash
python - <<'PY'
from pathlib import Path
import json

reports = [
    Path("output/getting_started/groundtruth/deterministic_station_report.json"),
    Path("output/getting_started/groundtruth/ai_station_report.json"),
    Path("output/getting_started/strict/deterministic_strict_station_report.json"),
    Path("output/getting_started/strict/ai_strict_station_report.json"),
]

print(",".join([
    "report",
    "method",
    "background_value",
    "mae_before",
    "rmse_before",
    "mae_after_conservative",
    "rmse_after_conservative",
    "mae_after_regularized",
    "rmse_after_regularized",
    "conservation_mae_conservative",
    "conservation_rmse_conservative",
    "conservation_mae_written",
    "conservation_rmse_written",
]))

for path in reports:
    if not path.exists():
        continue
    data = json.loads(path.read_text())
    before = data.get("station_metrics_before_correction", {})
    after_cons = data.get("station_metrics_after_correction_conservative", {})
    after_reg = data.get("station_metrics_after_correction_regularized", {})
    validation = data.get("conservation_validation", {})
    cons = validation.get("conservative_allocation", {})
    written = validation.get("written_regularized_output", {})
    row = [
        str(path),
        str(data.get("method", "")),
        str(data.get("background_value", "")),
        str(before.get("mae", "")),
        str(before.get("rmse", "")),
        str(after_cons.get("mae", "")),
        str(after_cons.get("rmse", "")),
        str(after_reg.get("mae", "")),
        str(after_reg.get("rmse", "")),
        str(cons.get("mae", "")),
        str(cons.get("rmse", "")),
        str(written.get("mae", "")),
        str(written.get("rmse", "")),
    ]
    print(",".join(row))
PY
```

Save the inline snippet output to a CSV file by redirecting the shell output:

```bash
python - <<'PY' > output/getting_started/metrics/quality_metrics.csv
from pathlib import Path
import json

reports = [
    Path("output/getting_started/groundtruth/deterministic_station_report.json"),
    Path("output/getting_started/groundtruth/ai_station_report.json"),
    Path("output/getting_started/strict/deterministic_strict_station_report.json"),
    Path("output/getting_started/strict/ai_strict_station_report.json"),
]

columns = [
    "report",
    "method",
    "background_value",
    "mae_before",
    "rmse_before",
    "mae_after_conservative",
    "rmse_after_conservative",
    "mae_after_regularized",
    "rmse_after_regularized",
    "conservation_mae_conservative",
    "conservation_rmse_conservative",
    "conservation_mae_written",
    "conservation_rmse_written",
]
print(",".join(columns))

for path in reports:
    if not path.exists():
        continue
    data = json.loads(path.read_text())
    before = data.get("station_metrics_before_correction", {})
    after_cons = data.get("station_metrics_after_correction_conservative", {})
    after_reg = data.get("station_metrics_after_correction_regularized", {})
    validation = data.get("conservation_validation", {})
    cons = validation.get("conservative_allocation", {})
    written = validation.get("written_regularized_output", {})
    print(",".join([
        str(path),
        str(data.get("method", "")),
        str(data.get("background_value", "")),
        str(before.get("mae", "")),
        str(before.get("rmse", "")),
        str(after_cons.get("mae", "")),
        str(after_cons.get("rmse", "")),
        str(after_reg.get("mae", "")),
        str(after_reg.get("rmse", "")),
        str(cons.get("mae", "")),
        str(cons.get("rmse", "")),
        str(written.get("mae", "")),
        str(written.get("rmse", "")),
    ]))
PY
```

The `background_value` column is the estimated average ground-level pollutant background from the station data. It is also printed during `--inspect-groundtruth` and during corrected downscaling runs.

## 13. Compute Raster Summary Statistics

Quality control should include raster-level statistics in addition to station metrics. The following snippet summarizes each output raster:

```bash
python - <<'PY'
from pathlib import Path
import numpy as np
import rasterio

rasters = sorted(Path("output/getting_started").glob("**/*.tif"))
print("raster,count,min,max,mean,median,std")
for path in rasters:
    with rasterio.open(path) as src:
        arr = src.read(1).astype(float)
        nodata = src.nodata
        mask = np.isfinite(arr)
        if nodata is not None and np.isfinite(nodata):
            mask &= arr != nodata
        vals = arr[mask]
        if vals.size == 0:
            print(f"{path},0,,,,,")
            continue
        print(",".join([
            str(path),
            str(vals.size),
            f"{np.nanmin(vals):.12g}",
            f"{np.nanmax(vals):.12g}",
            f"{np.nanmean(vals):.12g}",
            f"{np.nanmedian(vals):.12g}",
            f"{np.nanstd(vals):.12g}",
        ]))
PY
```

These statistics are not a replacement for conservation validation, but they help detect obvious scale errors, nodata mistakes, extreme values, and unexpected shifts introduced by station correction or deblocking.

## 14. Compare Deterministic And AI Outputs

Create AI-minus-deterministic difference rasters:

```bash
python - <<'PY'
from pathlib import Path
import numpy as np
import rasterio

pairs = [
    (
        "output/getting_started/no_groundtruth/ai_no2.tif",
        "output/getting_started/no_groundtruth/deterministic_no2.tif",
        "output/getting_started/metrics/diff_ai_minus_deterministic_no2.tif",
    ),
    (
        "output/getting_started/groundtruth/ai_no2_corrected.tif",
        "output/getting_started/groundtruth/deterministic_no2_corrected.tif",
        "output/getting_started/metrics/diff_ai_minus_deterministic_no2_corrected.tif",
    ),
]

for ai_path, det_path, out_path in pairs:
    ai_path = Path(ai_path)
    det_path = Path(det_path)
    out_path = Path(out_path)
    if not ai_path.exists() or not det_path.exists():
        continue
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(ai_path) as ai, rasterio.open(det_path) as det:
        a = ai.read(1).astype("float32")
        d = det.read(1).astype("float32")
        profile = ai.profile.copy()
        profile.update(dtype="float32", count=1, nodata=np.nan)
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write((a - d).astype("float32"), 1)
print("Wrote difference rasters in output/getting_started/metrics")
PY
```

Load the pollutant rasters, weight rasters, correction rasters, and difference rasters in QGIS or another GIS package. Use identical color ramps and min/max values when comparing methods.

Recommended visual checks:

- Are high values spatially plausible relative to land use, terrain, and meteorology?
- Are source-raster block boundaries visible?
- Does deblocking remove visual seams without erasing meaningful local structure?
- Does station correction create isolated artifacts around stations?
- Are deterministic and AI differences systematic, localized, or noisy?

## 15. Interpret Results Conservatively

SmokEye creates a model-assisted fine-grid allocation product. It does not create new satellite information at the `GEO.DAT` resolution.

Use the deterministic method as a transparent baseline. Use the AI method as a second model family for sensitivity analysis. Prefer a method only when several criteria agree:

- lower station error without unrealistic spatial artifacts;
- acceptable conservation validation;
- plausible relationship to terrain, land-use, and meteorological fields;
- stable behavior when deblocking is disabled;
- stable behavior across more than one date, pollutant, or meteorological episode.

When reporting results academically, state:

- the input raster product and pollutant band;
- the CALMET grid resolution, CRS, and time selection;
- whether station correction was used;
- the background estimation mode and value;
- whether seamless/deblocking regularization was used;
- station metrics before and after correction;
- conservation metrics for conservative and written outputs;
- known limitations, especially the difference between near-surface station measurements and satellite or model-layer quantities.

## 16. Minimal Reproducible Command Matrix

The following table summarizes the core onboarding runs.

```text
Purpose                              Method           Ground truth   Deblocking
deterministic baseline               deterministic    no             default
AI baseline                          ai               no             default
deterministic corrected              deterministic    yes            default
AI corrected                         ai               yes            default
deterministic strict baseline        deterministic    no             off
AI strict baseline                   ai               no             off
deterministic strict corrected       deterministic    yes            off
AI strict corrected                  ai               yes            off
```

Use `--validate` in every run that will be compared quantitatively. Use `--station-report` in every run with ground-truth correction. Use `--write-weight` and `--write-correction` whenever the spatial behavior of the method must be audited.
