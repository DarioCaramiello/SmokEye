# SmokEye Pollutant Downscaler

SmokEye provides one command with two comparable workflows for downscaling a gridded pollutant raster to the grid defined by a CALMET `GEO.DAT` file:

- `python downscale_pollutant.py --method deterministic`: deterministic dynamic downscaling with conservative allocation.
- `python downscale_pollutant.py --method ai`: AI-based dynamic downscaling with the same input interface and the same output products.

Both methods read the same pollutant raster, CALMET/CMET meteorology, `GEO.DAT` target grid, and optional station CSV. Both write a single-band GeoTIFF aligned to the `GEO.DAT` grid, plus optional diagnostic rasters and JSON reports. This makes the two methods suitable for direct side-by-side comparison.

The top-level script is a thin compatibility entry point. Shared implementation lives in the `smokeye` package so deterministic and AI workflows do not duplicate parsing, I/O, conservative allocation, station correction, validation, or raster writing code.

## What The Workflow Does

The command does not simply resample the source raster. It treats each source pixel value as a coarse observational constraint and distributes it over the finer CALMET grid using a weight field. The allocation is conservative before optional seamless/deblocking regularization:

```text
fine_i = source_P * w_i * sum(A_iP) / sum(w_i * A_iP)
```

where `w_i` is the fine-grid weight and `A_iP` is the overlap area between fine cell `i` and source pixel `P`.

The deterministic method builds `w_i` from explicit terrain, land-use, and meteorological rules. The AI method builds `w_i` using a deterministic machine-learning model while preserving the same downstream allocation, station-correction, reporting, and validation behavior.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On systems where `rasterio` needs GDAL-compatible wheels, a conda environment is often easier:

```bash
conda create -n smokeye -c conda-forge python=3.11 numpy rasterio shapely pyproj scipy
conda activate smokeye
```

## Quick Start

Inspect the target grid:

```bash
python downscale_pollutant.py --inspect-geodat data/geo.dat
```

Run deterministic downscaling:

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

Run AI downscaling with the same interface:

```bash
python downscale_pollutant.py --method ai \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/ai_no2.tif \
  --pollutant NO2 \
  --input-band 1 \
  --groundtruth-csv data/groundtruth.csv \
  --groundtruth-value-column NO2 \
  --validate \
  --station-report output/ai_station_report.json \
  --write-weight output/ai_weight.tif \
  --write-correction output/ai_correction.tif
```

## Documentation

- [Workflow overview](docs/workflow.md)
- [Input data requirements](docs/input-data.md)
- [Deterministic method](docs/deterministic-method.md)
- [AI method](docs/ai-method.md)
- [Step-by-step comparison guide](docs/comparison-guide.md)
- [Outputs, reports, and validation](docs/outputs-and-validation.md)

## Repository Layout

```text
SmokEye/
├── downscale_pollutant.py
├── smokeye/
│   ├── __init__.py
│   ├── cli.py
│   ├── downscaler.py
│   └── ai_downscaler.py
├── requirements.txt
├── README.md
├── AGENTS.md
├── docs/
│   ├── workflow.md
│   ├── input-data.md
│   ├── deterministic-method.md
│   ├── ai-method.md
│   ├── comparison-guide.md
│   └── outputs-and-validation.md
├── examples/
│   └── groundtruth_example.csv
└── data/
    ├── S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif
    ├── cmet.dat
    ├── geo.dat
    └── groundtruth.csv
```

## Development Notes

- Keep `downscale_pollutant.py` as a thin command-line wrapper.
- Put shared behavior in `smokeye/downscaler.py`.
- Put unified CLI dispatch in `smokeye/cli.py`.
- Put AI-specific weight-model behavior in `smokeye/ai_downscaler.py`.
- Do not duplicate source code between deterministic and AI workflows; add strategy hooks to the shared workflow when methods need to differ.

## Scientific Caveats

- A 200 m output grid does not mean the satellite observed the pollutant at 200 m resolution.
- The output is a model-assisted allocation product.
- Optional seamless/deblocking regularization improves visual continuity but relaxes strict per-source-pixel conservation.
- Station measurements are near-surface values, while some satellite products are column quantities. Station correction should be interpreted carefully.
- For production use, review the weight logic for the target pollutant, emissions regime, meteorology, and local land-use classes.

## License

This project is released under the MIT License. See `LICENSE`.
