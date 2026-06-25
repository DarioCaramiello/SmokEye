# Outputs, Reports, And Validation

Both downscaling methods write the same categories of output.

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

Compare deterministic and AI weight rasters to understand where the two methods differ before allocation.

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
- pollutant name and input band;
- station summary;
- background estimation settings and value;
- station metrics before correction;
- station metrics after correction before regularization;
- station metrics after correction in the written regularized output;
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
- `written_regularized_output`: final output after seamless/deblocking.

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

Use these metrics to compare station agreement, but interpret them carefully when station values are near-surface measurements and the raster is a satellite column or model-layer product.

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

For strict comparison of conservative allocation only, use `--no-seamless --deblock-sigma-m 0` in both deterministic and AI runs.
