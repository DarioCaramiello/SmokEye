# Deterministic Method

The deterministic method is the default mode:

```text
downscale_pollutant.py --method deterministic
```

It builds the fine-grid allocation weight from explicit rules. The resulting field is then used by the shared conservative allocation engine.

The entry-point script delegates through `smokeye/cli.py` to `smokeye/downscaler.py`, where the deterministic `build_weights` strategy and the shared workflow are implemented.

## Weight Components

The deterministic weight starts as ones and is modified by available local inputs.

### Land-Use

When land-use is available from `GEO.DAT`, class-specific factors are applied. Urban, built, or transport-like classes receive a modest enhancement; water and vegetation-like classes are reduced.

The factors are intentionally conservative and are meant to be reviewed for each domain.

### Terrain

When terrain is available from `GEO.DAT` or CALMET, lower-elevation cells receive a slight enhancement. This is a generic lowland/valley accumulation proxy and is not intended to replace a full dispersion model.

### Meteorology

Available meteorological modifiers include:

- `pblh`: lower boundary-layer height increases local accumulation.
- `ws10`: calmer wind conditions increase local accumulation.
- `ustar`: lower friction velocity increases local accumulation.

Each modifier is clipped so no single term dominates the weight field.

## Station Correction

When `--groundtruth-csv` is supplied, the deterministic method:

1. Builds the initial deterministic weight field.
2. Produces a first conservative downscaled field.
3. Samples that field at station locations.
4. Computes observed/predicted ratios.
5. Interpolates a smooth multiplicative station correction field.
6. Multiplies the weight field by the correction.
7. Re-runs conservative allocation.

The default ratio uses background-excess correction:

```text
ratio = max(obs - background, eps) / max(pred - background, eps)
```

Use direct ratios with:

```bash
--station-direct-ratio
```

## Example

```bash
python downscale_pollutant.py \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/deterministic_no2.tif \
  --pollutant NO2 \
  --input-band 1 \
  --groundtruth-csv data/groundtruth.csv \
  --groundtruth-value-column NO2 \
  --validate \
  --station-report output/deterministic_station_report.json \
  --write-weight output/deterministic_weight.tif \
  --write-correction output/deterministic_correction.tif
```

## Strengths

- Transparent and auditable.
- Easy to adapt for pollutant-specific rules.
- Good baseline for comparison and regression testing.

## Limitations

- Generic class factors may not match every domain.
- The rule set does not model chemistry, emissions, or full atmospheric transport.
- Weight factors must be reviewed before production scientific use.
