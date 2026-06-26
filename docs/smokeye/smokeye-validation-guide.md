# SmokEye Validation Guide

This guide explains how to use `smokeye-validation.py` as a single validation entry point for CALPUFF arbitrary-unit outputs, downscaled satellite products, and air-quality station data.

The script is intended to complement the existing SmokEye workflow:

1. `downscale_pollutant.py` creates a satellite-derived reference raster on the CALMET `GEO.DAT` grid.
2. `prepare_calpuff.py` creates a CALPUFF raster aligned with the same grid and time window.
3. `smokeye-validation.py` estimates a station-based calibration for CALPUFF arbitrary units and evaluates independent station skill.
4. `compare_calpuff_satellite.py` can then be used for additional pixel-by-pixel comparison between the calibrated CALPUFF raster and the downscaled satellite raster.

## 1. What the script does

`smokeye-validation.py` performs four operations:

1. Reads station observations from a CSV file.
2. Samples the raw CALPUFF raster and, optionally, the satellite/downscaled raster at station locations.
3. Splits station IDs into training and test groups.
4. Fits a pollutant-specific calibration:

```text
calibrated_CALPUFF = raw_CALPUFF * scale + offset + background
```

By default, only `scale` is fitted and `offset = 0`. This is the recommended first choice when stations are limited. Use `--fit-offset` only when there are enough independent observations.

The script writes:

```text
<out-prefix>.validation.json
<out-prefix>.stations.csv
<out-prefix>.calpuff_calibrated.tif   # only with --write-calibrated-raster
```

## 2. Required inputs

### 2.1 CALPUFF raster

The script expects a single-band raster containing CALPUFF model values. These may be arbitrary units.

You can obtain this raster by using `prepare_calpuff.py` with neutral conversion parameters, for example:

```bash
python prepare_calpuff.py \
  --calpuff calpuff.con \
  --geo GEO.DAT \
  --satellite outputs/no2_downscaled.tif \
  --species NO2 \
  --group TOTAL \
  --time-start 2025-02-25T07:00:00 \
  --time-end 2025-02-25T08:00:00 \
  --satellite-time-start 2025-02-25T07:00:00 \
  --satellite-time-end 2025-02-25T08:00:00 \
  --time-agg mean \
  --calpuff-unit arbitrary \
  --satellite-unit ug_m3 \
  --target-unit ug_m3 \
  --calpuff-scale 1.0 \
  --calpuff-offset 0.0 \
  --background 0.0 \
  --out-prefix outputs/no2_raw
```

The raster used by `smokeye-validation.py` would then be:

```text
outputs/no2_raw.model.tif
```

### 2.2 Satellite/downscaled raster

The satellite raster is optional but strongly recommended. It should be a SmokEye-downscaled product already aligned to the CALMET grid.

Example:

```bash
python downscale_pollutant.py \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  outputs/no2_downscaled.tif \
  --pollutant NO2 \
  --input-band 1 \
  --satellite-time-start 2024-06-28T11:00:00 \
  --satellite-time-end 2024-06-28T12:00:00 \
  --groundtruth-csv data/groundtruth.csv \
  --groundtruth-value-column NO2 \
  --validate \
  --station-report outputs/no2_downscaled.station_report.json
```

### 2.3 Station CSV

The station CSV must contain at least:

```text
station_id,lon,lat,NO2
```

Example:

```csv
station_id,lon,lat,NO2
ST001,14.255,40.842,34.1
ST002,14.301,40.867,28.4
ST003,14.188,40.821,41.7
```

The coordinate columns must be in the same coordinate reference system as the rasters. If the rasters are in projected coordinates, use projected `x,y`, not longitude/latitude.

You can change column names using:

```text
--station-id-column
--x-column
--y-column
--observed-column
```

## 3. Minimal command

For NO2:

```bash
python smokeye-validation.py \
  --pollutant NO2 \
  --calpuff-raw outputs/no2_raw.model.tif \
  --satellite outputs/no2_downscaled.tif \
  --stations data/stations_no2.csv \
  --station-id-column station_id \
  --x-column lon \
  --y-column lat \
  --observed-column NO2 \
  --out-prefix outputs/validation/no2
```

This writes:

```text
outputs/validation/no2.validation.json
outputs/validation/no2.stations.csv
```

The JSON contains the fitted scale, background, train/test station metrics, optional satellite-at-station metrics, and caveats.

## 4. Recommended command with calibrated raster output

To generate a calibrated CALPUFF raster for spatial comparison:

```bash
python smokeye-validation.py \
  --pollutant NO2 \
  --target-unit ug_m3 \
  --calpuff-raw outputs/no2_raw.model.tif \
  --satellite outputs/no2_downscaled.tif \
  --stations data/stations_no2.csv \
  --station-id-column station_id \
  --x-column lon \
  --y-column lat \
  --observed-column NO2 \
  --background 2.0 \
  --test-fraction 0.30 \
  --random-seed 42 \
  --hotspot-percentile 90 \
  --write-calibrated-raster \
  --out-prefix outputs/validation/no2
```

This additionally writes:

```text
outputs/validation/no2.calpuff_calibrated.tif
```

When `--write-calibrated-raster` is enabled and `--satellite` is provided, the JSON also includes raster-level spatial metrics and hotspot metrics.

## 5. Using explicit test stations

For a publication-quality analysis, a deterministic split is often better than a random split. You can choose the withheld stations explicitly:

```bash
python smokeye-validation.py \
  --pollutant PM10 \
  --calpuff-raw outputs/pm10_raw.model.tif \
  --satellite outputs/pm10_downscaled.tif \
  --stations data/stations_pm10.csv \
  --observed-column PM10 \
  --test-station-ids ST003,ST007,ST014 \
  --write-calibrated-raster \
  --out-prefix outputs/validation/pm10
```

All records belonging to those station IDs are treated as test data. All other station IDs are used for fitting the calibration.

## 6. Using pre-sampled station values

If you already have CALPUFF or satellite values extracted at station points, include them in the station CSV:

```csv
station_id,lon,lat,NO2,calpuff_raw,satellite_no2
ST001,14.255,40.842,34.1,1284.2,31.5
ST002,14.301,40.867,28.4,1102.8,26.1
```

Then run:

```bash
python smokeye-validation.py \
  --pollutant NO2 \
  --calpuff-raw outputs/no2_raw.model.tif \
  --satellite outputs/no2_downscaled.tif \
  --stations data/stations_no2_presampled.csv \
  --observed-column NO2 \
  --calpuff-at-station-column calpuff_raw \
  --satellite-at-station-column satellite_no2 \
  --out-prefix outputs/validation/no2_presampled
```

The raster paths are still retained in the report for provenance. The station calibration uses the CSV columns instead of sampling the rasters.

## 7. Optional fitted offset

By default, the calibration is:

```text
calibrated_CALPUFF = raw_CALPUFF * scale + background
```

Use a fitted offset only when the station network and episode count are sufficient:

```bash
python smokeye-validation.py \
  --pollutant CO \
  --calpuff-raw outputs/co_raw.model.tif \
  --satellite outputs/co_downscaled.tif \
  --stations data/stations_co.csv \
  --observed-column CO \
  --fit-offset \
  --write-calibrated-raster \
  --out-prefix outputs/validation/co
```

The fitted form becomes:

```text
calibrated_CALPUFF = raw_CALPUFF * scale + offset + background
```

Use this carefully. A fitted offset can absorb background errors, retrieval mismatch, or missing source terms. It should not be used to hide a structurally wrong simulation.

## 8. Output JSON structure

The validation JSON contains:

```json
{
  "pollutant": "NO2",
  "target_unit": "ug_m3",
  "station_count": 12,
  "train_station_count": 8,
  "test_station_count": 4,
  "calibration": {
    "scale": 0.0012,
    "offset": 0.0,
    "background": 2.0,
    "formula": "calibrated_CALPUFF = raw_CALPUFF * scale + offset + background"
  },
  "station_metrics_train": {},
  "station_metrics_test": {},
  "satellite_station_metrics": {},
  "spatial_metrics": {},
  "hotspot_metrics": {},
  "inputs": {},
  "caveats": []
}
```

Important fields:

- `calibration.scale`: conversion from arbitrary CALPUFF units to the target unit.
- `calibration.background`: background added after model-unit conversion.
- `station_metrics_test`: primary proof of predictive skill.
- `satellite_station_metrics`: how well the downscaled satellite product agrees with stations.
- `spatial_metrics`: raster-level comparison between calibrated CALPUFF and satellite reference, if enabled.
- `hotspot_metrics`: threshold-based spatial detection metrics.

## 9. Output station CSV

The station CSV contains one row per station record:

```text
station_id,split,observed,calpuff_raw,calpuff_calibrated,satellite_reference
```

Use this file to make scatterplots and residual diagnostics:

- observed vs calibrated CALPUFF;
- observed vs satellite reference;
- residual vs raw CALPUFF;
- residual by station;
- residual by wind sector, if joined with meteorological metadata.

## 10. Follow-up comparison with existing SmokEye tools

After generating the calibrated raster, use the existing comparison script:

```bash
python compare_calpuff_satellite.py \
  --model outputs/validation/no2.calpuff_calibrated.tif \
  --satellite outputs/no2_downscaled.tif \
  --out-prefix outputs/validation/no2_calpuff_vs_satellite
```

This creates:

```text
outputs/validation/no2_calpuff_vs_satellite.difference.tif
outputs/validation/no2_calpuff_vs_satellite.ratio.tif
outputs/validation/no2_calpuff_vs_satellite.stats.json
outputs/validation/no2_calpuff_vs_satellite.stats.csv
```

Use these outputs for maps, tables, and publication figures.

## 11. Recommended pollutant-specific commands

### NO2

```bash
python smokeye-validation.py \
  --pollutant NO2 \
  --calpuff-raw outputs/no2_raw.model.tif \
  --satellite outputs/no2_downscaled.tif \
  --stations data/stations_no2.csv \
  --observed-column NO2 \
  --background 2.0 \
  --write-calibrated-raster \
  --out-prefix outputs/validation/no2
```

Use NO2 as the primary combustion plume validation pollutant.

### CO

```bash
python smokeye-validation.py \
  --pollutant CO \
  --target-unit ug_m3 \
  --calpuff-raw outputs/co_raw.model.tif \
  --satellite outputs/co_downscaled.tif \
  --stations data/stations_co.csv \
  --observed-column CO \
  --background 0.0 \
  --write-calibrated-raster \
  --out-prefix outputs/validation/co
```

Use CO mainly for broad transport and background consistency.

### PM10

```bash
python smokeye-validation.py \
  --pollutant PM10 \
  --calpuff-raw outputs/pm10_raw.model.tif \
  --satellite outputs/pm10_downscaled.tif \
  --stations data/stations_pm10.csv \
  --observed-column PM10 \
  --background 5.0 \
  --test-fraction 0.40 \
  --hotspot-percentile 95 \
  --write-calibrated-raster \
  --out-prefix outputs/validation/pm10
```

Use PM10 with caution and rely heavily on station holdout validation.

## 12. Quality-control checklist

Before accepting a validation result, check:

- Station coordinates are in the same CRS as the raster.
- CALPUFF, satellite, and stations refer to the same time window or averaging period.
- CALPUFF scale was fitted only on training stations/times.
- Test stations were not used for station correction or calibration.
- Satellite downscaling validation was performed before using the satellite raster as a spatial reference.
- The fitted scale is positive and stable across episodes.
- Residuals are not dominated by one station or one episode.
- Results are stratified by wind sector or meteorological regime when possible.

## 13. Common mistakes

### Comparing raw CALPUFF arbitrary units directly to satellite values

This is not valid. Use station-based calibration first.

### Fitting scale against the full satellite raster

This is circular if the same satellite raster is later used as proof of spatial agreement. Fit against training stations, then use satellite rasters for independent spatial diagnostics.

### Treating downscaled satellite pixels as true fine-resolution observations

Downscaled satellite products are model-assisted allocations. They improve spatial diagnostic value but do not turn a coarse satellite observation into a true fine-resolution measurement.

### Ignoring background

For CO and PM10 especially, background concentration can dominate the comparison. Use documented fixed background, station-derived background, or external regional background, and report the choice.

## 14. Suggested repository commit message

```text
Add CALPUFF station/satellite validation entry point

- Add smokeye-validation.py for station-based calibration of CALPUFF arbitrary units
- Add holdout station metrics, optional calibrated raster output, spatial metrics, and hotspot metrics
- Add academic validation strategy and step-by-step guide
```
