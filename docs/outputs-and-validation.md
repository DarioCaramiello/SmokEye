# Outputs, Reports, And Validation

All downscaling methods write the same categories of output. This common output contract supports direct comparison among deterministic, AI, and diffusion runs, provided that the same input raster, target grid, meteorological time selection, units, and regularization settings are used.

## Main GeoTIFF

The positional `output_tif` is a single-band GeoTIFF aligned to the `GEO.DAT` grid.

Properties:

- CRS: target grid CRS.
- transform: target grid transform.
- width/height: target grid dimensions.
- dtype: `float32`.
- nodata: `NaN`.
- compression: Deflate.

## Optional Weight Raster

Use:

```bash
--write-weight output/weight.tif
```

This writes the final dynamic weight field. If station correction is used, this is the corrected weight field.

Compare deterministic, AI, and diffusion diagnostic rasters to understand where the methods differ before final validation. For deterministic and AI runs, the weight raster shows the allocation surface directly; for diffusion runs, pair the weight raster with uncertainty or ensemble outputs when those diagnostics are written.

The weight raster is also the primary diagnostic for array-orientation problems. For deterministic runs, a vertically mirrored high-resolution pattern usually points to `--geodat-array-origin` or `--calmet-array-origin`. Run a strict diagnostic output with:

```bash
--write-weight output/weight.tif --no-seamless --deblock-sigma-m 0
```

## Optional Station Correction Raster

Use:

```bash
--write-correction output/correction.tif
```

This writes the multiplicative station correction field. Values above `1` increase the local dynamic weight; values below `1` decrease it.

## Optional Station Report

Use:

```bash
--station-report output/station_report.json
```

The JSON report includes:

- inferred grid metadata;
- meteorology fields used;
- `GEO.DAT` and CALMET array-origin choices;
- pollutant name and input band;
- station summary;
- background estimation settings and value;
- station metrics before correction;
- station metrics after correction before regularization;
- station metrics after correction in the written regularized and normalized output;
- station correction details;
- conservation validation when `--validate` is used.

## Validation Output

Use:

```bash
--validate
```

The command recomputes coarse-scale means of the fine output inside each source pixel and compares them to the original source pixel values.

The validation block contains:

```text
n
bias
mae
rmse
```

When regularization is enabled, validation is reported for:

- `conservative_allocation`: exact allocation before regularization.
- `written_regularized_normalized_output`: final output after seamless/deblocking and hard coarse-to-fine normalization.

## Station Metrics

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

Use these metrics to compare station agreement, but interpret them carefully when station values are near-surface measurements and the raster is a satellite column or model-layer product. For publication-quality validation, reserve independent station records for testing whenever station observations have also been used for calibration or correction.

## Seamless And Deblocking Options

Default regularization is enabled:

```bash
--seamless
--seamless-baseline-sigma-m 1400
--seamless-anomaly-sigma-m 1000
--seamless-strength 0.95
--deblock-sigma-m 400
--deblock-strength 0.75
--deblock-iterations 1
```

Disable seamless recomposition:

```bash
--no-seamless
```

Disable final deblocking:

```bash
--deblock-sigma-m 0
```

Relax final coarse-to-fine conservation normalization for visualization or diagnostic products:

```bash
--conservation-relaxation 0.5
```

The default `--conservation-relaxation 0` keeps strict hard normalization. `--conservation-relaxation 1` writes the regularized field without final source-pixel rescaling, and intermediate values blend the two fields. Nonzero values intentionally relax conservation, so the `written_regularized_normalized_output` validation block should be interpreted as the documented conservation deviation of that product.

For a comparison of the allocation stage without visual regularization, use `--no-seamless --deblock-sigma-m 0` in both deterministic and AI runs.

## CALPUFF Comparison Outputs

`prepare_calpuff.py` writes three files for each `--out-prefix`, and `compare_calpuff_satellite.py` writes the comparison products:

```text
<prefix>.model.tif       CALPUFF values converted to target units, background-added, and aligned to the reference grid
<prefix>.satellite.tif   reference GeoTIFF values converted to target units
<prefix>.prepare.json    preparation provenance, time selection, and unit-conversion diagnostics
<prefix>.difference.tif  model minus reference
<prefix>.ratio.tif       model divided by reference, with NaN where the reference is zero or invalid
<prefix>.stats.json      comparison provenance and diagnostics
<prefix>.stats.csv       flat metric/value statistics table
```

The GeoTIFF products use the reference raster grid, CRS, transform, dimensions, `float32` dtype, `NaN` nodata, and Deflate compression.

The preparation JSON report contains:

- `calpuff`: input path, species, group, level, and aggregation method.
- `calpuff_time_selection`: selected record times, overlap weights, or closest-record delta.
- `geo`: `GEO.DAT` grid metadata from `GeoDATReader`.
- `satellite`: reference path and band.
- `time_check`: satellite/reference time source, CALPUFF comparison window, overlap seconds, overlap fraction, policy, and status.
- `unit_conversion`: raw/converted CALPUFF stats, model-after-background stats, raw/converted satellite stats, scale/offset values, target unit, and formula.
- `notes`: scientific caveats that should accompany the comparison.

The comparison JSON report contains prepared model/reference paths, optional embedded preparation metadata, `statistics` with valid-pixel count, min/max/mean/std, bias, MAE, RMSE, correlation, mean ratio, and median ratio, plus the scientific caveats.
