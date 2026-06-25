# Workflow Overview

SmokEye downscales a coarse pollutant raster to a CALMET `GEO.DAT` grid. It provides two methods with the same operational shape:

- Deterministic method: explicit rule-based dynamic weights.
- AI method: deterministic machine-learning dynamic weights.

Both methods use the same conservative allocation engine after the weight field is built.

## Implementation Layout

The single top-level script is a compatibility entry point:

- `downscale_pollutant.py` defaults to the shared deterministic workflow.
- `downscale_pollutant.py --method ai` calls the shared workflow with the AI weight builder.

Unified method dispatch lives in `smokeye/cli.py`. Shared readers, allocation, station correction, validation, deblocking, and raster writers live in `smokeye/downscaler.py`. The AI-only weight strategy lives in `smokeye/ai_downscaler.py`. This keeps method differences explicit while avoiding duplicate source code.

## Processing Stages

1. Read the selected pollutant band from the input GeoTIFF.
2. Infer the target grid, CRS, resolution, terrain, and land-use from `GEO.DAT`.
3. Read meteorological fields from CALMET/CMET binary records or from an optional `.npz`.
4. Build a fine-grid weight field on the target grid.
5. Conservatively redistribute each source pixel to overlapping target cells.
6. Optionally estimate a background pollutant value from station measurements.
7. Optionally build and apply a station correction field.
8. Optionally apply seamless/deblocking regularization to reduce coarse-pixel seams.
9. Write a single-band GeoTIFF and optional diagnostics.

## Shared Command-Line Contract

The command accepts the same positional arguments for both methods:

```text
input_tif calmet_dat geodat output_tif
```

Both methods also accept the same flags for pollutant selection, station correction, validation, deblocking, diagnostics, and inspection modes. This is deliberate: a deterministic run and an AI run can be produced by changing only `--method` and output paths.

## Conservation Behavior

The conservative allocation stage preserves the source field at coarse-pixel scale before optional regularization. When `--validate` is used, the command reports:

- `conservative_allocation`: validation of the exact allocation before regularization.
- `written_regularized_output`: validation of the final written output after seamless/deblocking regularization.

The second statistic may have larger differences because regularization is allowed to smooth coarse-pixel boundaries.

## When To Use Each Method

Use the deterministic method when you want a transparent rule-based baseline. It is easier to audit because each modifier is explicit.

Use the AI method when you want a second model family for sensitivity analysis. It uses the same input data and output contract, but the fine-grid weight surface is produced by a compact nonlinear model.
