# Diffusion Method

The diffusion method is selected with:

```text
downscale_pollutant.py --method diffusion
```

It uses the same core inputs and outputs as the deterministic and AI methods, but adds a checkpoint-driven residual generation step before writing the final raster. In scientific use, the checkpoint is part of the method definition: its training data, training strategy, seed policy, and version should be retained with the output provenance.

## Inference

Diffusion inference requires an explicit checkpoint:

```bash
python downscale_pollutant.py --method diffusion \
  data/S5P_NO2_000_20240628T111519UTC_orbit-unknown.tif \
  data/cmet.dat \
  data/geo.dat \
  output/diffusion_no2.tif \
  --pollutant NO2 \
  --diffusion-checkpoint runs/diffusion_hybrid/best.pt \
  --diffusion-samples 8 \
  --diffusion-seed 42 \
  --validate
```

If `--diffusion-checkpoint` is omitted, the command fails instead of silently falling back to deterministic output.

The implementation supports these diffusion-specific options:

```text
--diffusion-checkpoint PATH
--diffusion-samples N
--diffusion-seed INTEGER
--diffusion-device cpu|cuda|mps|auto
--diffusion-steps INTEGER
--diffusion-guidance-scale FLOAT
--diffusion-training-strategy STRATEGY
--diffusion-train
--diffusion-train-config PATH
--diffusion-train-output-dir PATH
--write-uncertainty
--write-ensemble
```

## Conservation

The diffusion method treats the deterministic SmokEye field as the physically interpretable baseline:

```text
deterministic conservative field
  -> positive residual diffusion structure
  -> non-negativity enforcement
  -> hard coarse-to-fine conservative normalization
```

The final normalization rescales the generated fine-grid values inside each source pollutant pixel footprint so the area-weighted fine mean matches the original coarse pixel value. This hard step is applied after seamless/deblocking and residual generation, so the written diffusion GeoTIFF is the conservation-enforced product. If an experiment deliberately relaxes this invariant, the relaxation value and validation errors must be reported as a non-conservative diagnostic condition.

## Lightweight Checkpoints

Production checkpoints may be produced by an external diffusion training stack. For lightweight experiments, JSON or NPZ checkpoints can provide these scalar controls:

```json
{
  "residual_scale": 0.08,
  "residual_sigma_m": 900.0,
  "weight_influence": 0.35
}
```

Other checkpoint suffixes are accepted as explicit checkpoint paths, but SmokEye does not train or deserialize a deep-learning model internally.

## Training Strategies

The CLI records the selected training strategy and validates the training configuration path:

```bash
python downscale_pollutant.py \
  --method diffusion \
  --diffusion-train \
  --diffusion-training-strategy hybrid_teacher_student \
  --diffusion-train-config examples/diffusion/hybrid_teacher_student.yaml \
  --diffusion-train-output-dir runs/diffusion_hybrid
```

Supported strategies are:

- `self_supervised_coarse_to_fine`: train from fine historical reference fields degraded to synthetic coarse inputs.
- `hybrid_teacher_student`: train residuals or redistribution variability from deterministic SmokEye teacher fields, optionally station-corrected.
- `physics_guided_weak_supervision`: train with weak conservation, station, non-negativity, smoothness, meteorological, and land-use constraints.

Training mode writes `training_manifest.json` in the requested output directory. Full neural-network optimization remains external to this lightweight command-line package.

## Output Tags

Diffusion output GeoTIFFs include tags similar to:

```text
method=diffusion_conservation_guided_downscaling
diffusion_model=residual_conditional_diffusion_checkpoint
conservation=hard_coarse_to_fine_normalization
```

## Reporting Guidance

Academic reports should identify the diffusion checkpoint, training strategy, inference seed, sample count, uncertainty or ensemble outputs, and conservation-validation statistics. Diffusion-assisted structure should be interpreted as conditional model-generated fine-scale variability, not as newly observed sub-pixel satellite information.
