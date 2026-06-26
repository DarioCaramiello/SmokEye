# SmokEye Pollutant Downscaler

SmokEye provides one command with three comparable workflows for downscaling a gridded pollutant raster to the grid defined by a CALMET `GEO.DAT` file. The package is designed for reproducible scientific comparison: all methods share the same grid readers, temporal selection rules, conservative allocation engine, station-correction workflow, validation reports, and raster writers.

- `python downscale_pollutant.py --method deterministic`: deterministic dynamic downscaling with conservative allocation.
- `python downscale_pollutant.py --method ai`: AI-based dynamic downscaling with the same input interface and the same output products.
- `python downscale_pollutant.py --method diffusion`: conservation-guided residual diffusion downscaling with hard coarse-to-fine normalization.

All methods read the same pollutant raster, CALMET/CMET meteorology, `GEO.DAT` target grid, and optional station CSV. They write a single-band GeoTIFF aligned to the `GEO.DAT` grid, plus optional diagnostic rasters and JSON reports. This makes the methods suitable for direct side-by-side comparison under fixed assumptions about time, units, coordinate reference system, array orientation, and conservation.

Downscaling enforces timestamp consistency between the pollutant raster and weather data. SmokEye uses explicit `--satellite-time-start/--satellite-time-end` values or common GeoTIFF time metadata, then selects the closest CALMET records using the file timestamp encoding. `--calmet-stamp-format auto` infers either `YYYYMMDDHH` stamps such as `2024062811` or CALMET/CMET `YYYYJJJHHH` stamps such as `202418011`; override it when a producer documents a specific encoding. The satellite time parameters are ISO datetimes such as `2024-06-28T11:00:00`, `2024-06-28T11:00:00Z`, or `2024-06-28T13:00:00+02:00`; timezone-aware values are converted to UTC before use. Start and end must be supplied together, and the end must be after the start unless an instant timestamp is intentionally expanded with `--satellite-instant-duration-minutes`. Untimed pollutant rasters require `--allow-untimed-satellite`.

GEO.DAT terrain/land-use arrays and CALMET gridded records can be stored south-to-north or already north-to-south depending on the producer. SmokEye defaults to the historical `lower` storage assumption and flips those arrays into raster row order. If diagnostic weight rasters look vertically mirrored, run with `--geodat-array-origin upper` and/or `--calmet-array-origin upper` after confirming the source file order.

Pollutant concentrations are treated and written as micrograms per cubic meter (`ug_m3`) by default. If an input product is in another unit, convert it before downscaling or use the explicit scale/offset controls in `prepare_calpuff.py` so the final comparison unit remains `ug_m3`.

The top-level script is a thin compatibility entry point. Shared implementation lives in the `smokeye` package so deterministic and AI workflows do not duplicate parsing, I/O, conservative allocation, station correction, validation, or raster writing code.

## What The Workflow Does

The command does not simply resample the source raster. It treats each source pixel value as a coarse observational constraint and distributes it over the finer CALMET grid using a weight field. The allocation is conservative, optional seamless/deblocking regularization is applied, and by default the written raster is hard-normalized back to the original source-pixel means:

```text
fine_i = source_P * w_i * sum(A_iP) / sum(w_i * A_iP)
```

where `w_i` is the fine-grid weight and `A_iP` is the overlap area between fine cell `i` and source pixel `P`.

The deterministic method builds `w_i` from explicit terrain, land-use, and meteorological rules. The AI method builds `w_i` using a deterministic machine-learning model while preserving the same downstream allocation, station-correction, reporting, validation, and final conservation-normalization behavior. The diffusion method starts from the deterministic conservative field, generates positive residual fine-grid structure from an explicit checkpoint, and uses the same hard normalization so each source pollutant pixel footprint aggregates back to the original coarse value.

For visualization or diagnostic products where visible coarse-pixel seams are more important than exact coarse-to-fine conservation, use `--conservation-relaxation VALUE` with `VALUE` between `0` and `1`. The default `0` keeps strict hard normalization. A value of `1` writes the regularized field without final source-pixel rescaling, and intermediate values blend the strict and relaxed fields.

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

If embedded GEO.DAT arrays are already in north-to-south raster order:

```bash
python downscale_pollutant.py --inspect-geodat data/geo.dat --geodat-array-origin upper
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
  --satellite-time-start 2024-06-28T11:00:00 \
  --satellite-time-end 2024-06-28T12:00:00 \
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
  --satellite-time-start 2024-06-28T11:00:00 \
  --satellite-time-end 2024-06-28T12:00:00 \
  --groundtruth-csv data/groundtruth.csv \
  --groundtruth-value-column NO2 \
  --validate \
  --station-report output/ai_station_report.json \
  --write-weight output/ai_weight.tif \
  --write-correction output/ai_correction.tif
```

Run diffusion downscaling with an explicit checkpoint:

```bash
python downscale_pollutant.py --method diffusion \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/diffusion_no2.tif \
  --pollutant NO2 \
  --input-band 1 \
  --satellite-time-start 2024-06-28T11:00:00 \
  --satellite-time-end 2024-06-28T12:00:00 \
  --diffusion-checkpoint runs/diffusion_hybrid/best.pt \
  --diffusion-samples 8 \
  --diffusion-seed 42 \
  --write-uncertainty \
  --validate
```

Diffusion inference fails with a clear error if `--diffusion-checkpoint` is omitted. This prevents deterministic output from being mislabeled as diffusion-assisted output.

### Preparing and comparing CALPUFF output with satellite/downscaled pollutant GeoTIFFs

SmokEye uses two explicit CALPUFF steps. `prepare_calpuff.py` reads CALPUFF gridded outputs, selects the requested pollutant/time window, applies documented unit conversions, and writes CALPUFF and satellite rasters on the same grid. `compare_calpuff_satellite.py` then compares those prepared rasters pixel by pixel.

```bash
python prepare_calpuff.py \
  --calpuff calpuff.con \
  --geo GEO.DAT \
  --satellite final_weight_gt_deblocked.tif \
  --species NO2 \
  --group TOTAL \
  --time-start 2025-02-25T07:00:00 \
  --time-end 2025-02-25T08:00:00 \
  --satellite-time-start 2025-02-25T07:00:00 \
  --satellite-time-end 2025-02-25T08:00:00 \
  --time-agg mean \
  --time-selection closest \
  --calpuff-unit arbitrary \
  --satellite-unit ug_m3 \
  --target-unit ug_m3 \
  --calpuff-scale 0.001 \
  --calpuff-offset 0.0 \
  --satellite-scale 1.0 \
  --satellite-offset 0.0 \
  --background 2.0 \
  --out-prefix outputs/no2_total_vs_satellite

python compare_calpuff_satellite.py \
  --model outputs/no2_total_vs_satellite.model.tif \
  --satellite outputs/no2_total_vs_satellite.satellite.tif \
  --preparation-report outputs/no2_total_vs_satellite.prepare.json \
  --out-prefix outputs/no2_total_vs_satellite
```

The model compared against the satellite is:

```text
model = raw_CALPUFF * calpuff_scale + calpuff_offset + background
satellite = raw_satellite * satellite_scale + satellite_offset
```

`background` is always expressed in the final target unit, `ug_m3` by default, and is added after CALPUFF unit conversion. SmokEye does not guess conversions between arbitrary CALPUFF units, near-surface concentrations, deposition fluxes, mixing ratios, and satellite column amounts; those conversions must be supplied explicitly through scale/offset and documented in the JSON report.

The preparation command writes aligned `.model.tif`, `.satellite.tif`, and `.prepare.json` outputs using the supplied prefix. The comparison command writes `.difference.tif`, `.ratio.tif`, `.stats.json`, and `.stats.csv`. Use `prepare_calpuff.py --list-records` to inspect available CALPUFF species/group/time records without requiring a satellite raster.

By default, the comparison is time-strict. SmokEye fails unless the CALPUFF comparison window and the satellite/reference validity window overlap by at least `--min-time-overlap-fraction` of the satellite window. Use `--time-overlap-policy warn` only for documented diagnostics, and `--allow-untimed-satellite` only when the missing satellite/reference time is an explicit assumption.


## Documentation

- [Workflow overview](docs/workflow.md)
- [Getting started](docs/getting_started.md)
- [Input data requirements](docs/input-data.md)
- [Deterministic method](docs/deterministic-method.md)
- [AI method](docs/ai-method.md)
- [Diffusion method](docs/diffusion-method.md)
- [CALPUFF comparison](docs/calpuff-comparison.md)
- [Step-by-step comparison guide](docs/comparison-guide.md)
- [Outputs, reports, and validation](docs/outputs-and-validation.md)

## Repository Layout

```text
SmokEye/
├── downscale_pollutant.py
├── prepare_calpuff.py
├── compare_calpuff_satellite.py
├── smokeye/
│   ├── __init__.py
│   ├── cli.py
│   ├── ai_downscaler.py
│   ├── diffusion_downscaler.py
│   ├── downscaler.py
│   └── ...
├── requirements.txt
├── README.md
├── AGENTS.md
├── docs/
│   ├── workflow.md
│   ├── getting_started.md
│   ├── input-data.md
│   ├── deterministic-method.md
│   ├── ai-method.md
│   ├── diffusion-method.md
│   ├── calpuff-comparison.md
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
- Optional seamless/deblocking regularization improves visual continuity; the final written raster is then normalized so source-pixel conservation remains enforced.
- All methods write conservation-enforced products; validation reports both the initial conservative allocation and the final regularized, normalized output.
- Station measurements are near-surface values, while some satellite products are column quantities. Station correction should be interpreted carefully.
- For production use, review the weight logic for the target pollutant, emissions regime, meteorology, and local land-use classes.

## References

SmokEye's conservative allocation, ancillary-data weighting, and diffusion-assisted workflow are informed by the following peer-reviewed journal articles and conference proceedings:

- Tobler, W. R. (1979). Smooth pycnophylactic interpolation for geographical regions. *Journal of the American Statistical Association*, 74(367), 519-530. https://doi.org/10.1080/01621459.1979.10481647
- Eicher, C. L., & Brewer, C. A. (2001). Dasymetric mapping and areal interpolation: Implementation and evaluation. *Cartography and Geographic Information Science*, 28(2), 125-138. https://doi.org/10.1559/152304001782173727
- Mennis, J. (2003). Generating surface models of population using dasymetric mapping. *The Professional Geographer*, 55(1), 31-42. https://doi.org/10.1111/0033-0124.10042
- Song, Y., & Ermon, S. (2019). Generative modeling by estimating gradients of the data distribution. *Advances in Neural Information Processing Systems*, 32.
- Ho, J., Jain, A., & Abbeel, P. (2020). Denoising diffusion probabilistic models. *Advances in Neural Information Processing Systems*, 33, 6840-6851.
- Rombach, R., Blattmann, A., Lorenz, D., Esser, P., & Ommer, B. (2022). High-resolution image synthesis with latent diffusion models. *Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition*, 10684-10695. https://doi.org/10.1109/CVPR52688.2022.01042

## License

This project is released under the MIT License. See `LICENSE`.
