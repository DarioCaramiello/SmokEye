# Workflow Overview

SmokEye downscales a coarse pollutant raster to a CALMET `GEO.DAT` grid. It provides three methods with the same operational contract:

- Deterministic method: explicit rule-based dynamic weights.
- AI method: deterministic machine-learning dynamic weights.
- Diffusion method: checkpoint-driven residual structure with conservation-guided normalization.

All methods use the same conservative allocation engine after the method-specific fine-grid structure or weight field is built. This shared contract is central to scientific comparability: differences among outputs should be attributable to the documented method strategy, not to divergent I/O, station correction, validation, or raster-writing code paths.

## Implementation Layout

The top-level scripts are compatibility entry points:

- `downscale_pollutant.py` defaults to the shared deterministic workflow.
- `downscale_pollutant.py --method ai` calls the shared workflow with the AI weight builder.
- `downscale_pollutant.py --method diffusion` calls the shared workflow and then applies checkpoint-driven residual generation before final conservation normalization.
- `prepare_calpuff.py` prepares CALPUFF outputs as satellite-aligned rasters with explicit time and unit handling.
- `compare_calpuff_satellite.py` compares the prepared CALPUFF and satellite/reference rasters.

Unified method dispatch lives in `smokeye/cli.py`. Shared readers, allocation, station correction, validation, deblocking, and raster writers live in `smokeye/downscaler.py`. CALPUFF preparation and prepared-raster statistics live in `smokeye/comparison.py`. Method-specific strategies live in small modules such as `smokeye/ai_downscaler.py` and `smokeye/diffusion_downscaler.py`. This keeps scientific differences explicit while avoiding duplicate source code.

## Processing Stages

1. Read the selected pollutant band from the input GeoTIFF.
2. Infer the target grid, CRS, resolution, terrain, and land-use from `GEO.DAT`.
3. Read meteorological fields from CALMET/CMET binary records or from an optional `.npz`, selecting the requested CALMET time record when multiple records are present.
4. Build a fine-grid weight field on the target grid.
5. Conservatively redistribute each source pixel to overlapping target cells.
6. Optionally estimate a background pollutant value from station measurements.
7. Optionally build and apply a station correction field.
8. Optionally apply seamless/deblocking regularization to reduce coarse-pixel seams.
9. Hard-normalize the final field so each source pollutant pixel footprint aggregates back to the original coarse value.
10. Write a single-band GeoTIFF and optional diagnostics.

## Temporal Model

Each SmokEye command represents one pollutant analysis time or one pre-aggregated time window. The command reads explicit `--satellite-time-start/--satellite-time-end` values or common GeoTIFF time metadata and does not infer a timestamp from filenames. The user is responsible for giving mutually consistent inputs:

- the selected pollutant raster band should represent the target analysis time or averaging period;
- pollutant concentration values should be in micrograms per cubic meter (`ug_m3`) unless an explicit preprocessing or conversion step documents otherwise;
- CALMET/CMET meteorology is selected for the same or closest allowed timestamp unless `--calmet-selector mean` is used for a matching average period;
- station measurements should be prefiltered or pre-aggregated to the same time basis before they are passed with `--groundtruth-csv`;
- NPZ meteorology files are treated as already time-selected arrays on the `GEO.DAT` grid.

`--satellite-time-start` and `--satellite-time-end` accept ISO datetime strings such as `2024-06-28T11:00:00`, `2024-06-28 11:00:00`, `2024-06-28T11:00:00Z`, or `2024-06-28T13:00:00+02:00`. If a timezone is supplied, SmokEye converts the value to UTC before dropping timezone information internally. The two values must be provided together; the end must be greater than the start, except that equal start/end values may represent an instant and can be expanded with `--satellite-instant-duration-minutes`.

For CALMET/CMET binary inputs, SmokEye reads all supported records for each meteorological field label and then chooses one array per field:

- `--calmet-selector mean` uses the cellwise mean across all supported records for each field;
- `--calmet-stamp INTEGER` overrides the satellite-derived target stamp and chooses the record whose CALMET integer timestamp is nearest to `INTEGER`;
- `--calmet-stamp-format auto|yyyymmddhh|yyyydddhhh` controls how CALMET stamps are decoded;
- `--max-calmet-stamp-delta INTEGER` fails the command when the nearest available CALMET record is not close enough in hours.

The CALMET timestamp is read from the 4-byte integer following the 8-byte field label in each supported gridded record. For automatic selection, SmokEye infers whether nonzero file stamps are calendar `YYYYMMDDHH` values or CALMET/CMET `YYYYJJJHHH` values, converts the satellite/reference midpoint to the resolved format, and records the chosen meteorology stamps in output diagnostics. Static records with stamp `0`, such as roughness, land use, elevation, or LAI, are accepted as static fields and are not compared against the requested time. Record selection is performed independently for each meteorological field, so datasets with missing fields at some times should be inspected carefully.

## Grid Array Orientation

Raster row 0 is the northern/top row. Some CALMET-family files store gridded values from the southern/lower row upward, while others are already in raster order. SmokEye exposes this explicitly:

```bash
--geodat-array-origin lower
--calmet-array-origin lower
```

These defaults preserve historical behavior and flip source arrays into raster order. Use `upper` for a source whose arrays are already north-to-south. The selected origins are recorded in station reports and output tags. When a deterministic output shows a vertically mirrored fine-grid structure, inspect `--write-weight` output with `--no-seamless --deblock-sigma-m 0` before changing scientific weighting rules.

## Shared Command-Line Contract

The command accepts the same positional arguments for both methods:

```text
input_tif calmet_dat geodat output_tif
```

All methods accept the same common flags for pollutant selection, station correction, validation, deblocking, diagnostics, and inspection modes. This is deliberate: deterministic, AI, and diffusion runs can be compared by changing only the method-specific options and output paths.

## Conservation Behavior

The conservative allocation stage preserves the source field at coarse-pixel scale before optional regularization. After regularization, SmokEye normalizes the final field back to the original source-pixel means. When `--validate` is used, the command reports:

- `conservative_allocation`: validation of the exact allocation before regularization.
- `written_regularized_normalized_output`: validation of the final written output after seamless/deblocking regularization and hard normalization.

Both statistics should remain near numerical precision for valid overlapping source pixels. A larger final error indicates a conservation or metadata problem, not an expected smoothing effect.

## When To Use Each Method

Use the deterministic method when you want a transparent rule-based baseline. It is easier to audit because each modifier is explicit.

Use the AI method when you want a second model family for sensitivity analysis. It uses the same input data and output contract, but the fine-grid weight surface is produced by a compact nonlinear model.

Use the diffusion method when you have an explicit checkpoint and want to test conservation-guided residual fine-scale structure. Treat diffusion outputs as model-assisted realizations whose scientific credibility depends on the training strategy, checkpoint provenance, ensemble diagnostics, and the final coarse-to-fine conservation report.
