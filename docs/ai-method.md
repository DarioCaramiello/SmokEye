# AI Method

The AI method is selected with:

```text
downscale_pollutant.py --method ai
```

It has the same command-line interface and writes the same output types as the deterministic method. The main difference is how the fine-grid dynamic weight field is produced.

The entry-point script delegates through `smokeye/cli.py` to `smokeye/ai_downscaler.py`, which provides the AI weight strategy to the shared workflow in `smokeye/downscaler.py`. The shared workflow owns argument parsing, readers, allocation, station correction, validation, deblocking, and output writing.

## Model Type

The current AI implementation uses a deterministic Extreme Learning Machine-style model:

1. Build feature vectors for every target-grid cell.
2. Create a fixed random nonlinear hidden layer using a reproducible seed.
3. Fit the output layer by ridge regression.
4. Predict a positive weight for every target-grid cell.
5. Smooth and clip the learned weight field for stable conservative allocation.

The random hidden layer is deterministic because it uses a fixed seed. Re-running the same inputs produces the same weight field.

## Features

The AI model can use:

- normalized grid coordinates;
- coordinate interactions;
- terrain/elevation;
- land-use code;
- land-use class indicators;
- CALMET or NPZ meteorological fields.

Only fields present on the target grid are included.

## Training Signal

The model is fitted to a physically informed teacher field produced from the same inputs. This makes the AI method an alternate nonlinear approximation of the dynamic allocation surface while preserving a comparable workflow.

Station correction, conservative allocation, seamless/deblocking regularization, output writing, and validation are then handled by the same code paths as the deterministic workflow.

## Example

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

During execution, the AI method prints a short model summary like:

```text
AI weight model: features=22 hidden=88 training_cells=10000
```

## Output Tags

The AI output GeoTIFF includes method tags identifying the AI workflow:

```text
method=ai_conservative_dynamic_downscaling
ai_model=deterministic_extreme_learning_machine_ridge
```

## Strengths

- Same inputs and outputs as the deterministic method.
- Nonlinear feature interactions can produce a different allocation surface.
- Deterministic and reproducible for comparison experiments.

## Limitations

- The current model is not trained on an independent historical dataset.
- Because the teacher field is derived from the deterministic model, the AI method is best interpreted as an alternate model-family sensitivity test, not proof of higher physical accuracy.
- For a fully supervised AI downscaler, replace or extend the training signal with historical high-resolution analyses, emissions inventory data, station networks, or chemistry-transport model outputs.
