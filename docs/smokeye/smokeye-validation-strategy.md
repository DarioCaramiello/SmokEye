# SmokEye Validation Strategy for CALPUFF Using Downscaled Satellite and Station Data

## Abstract

This document defines a validation strategy for demonstrating whether CALPUFF is working well when its native output is available only in arbitrary model units and when NO2, CO, and PM10 are available from both satellite products and air-quality stations. The proposed strategy treats CALPUFF as a spatial-temporal dispersion predictor whose arbitrary values must first be calibrated against independent near-surface observations. Downscaled satellite products are then used primarily to test spatial structure, plume placement, hotspot behavior, and episode consistency on the CALMET/SmokEye grid. The strategy is designed for integration with SmokEye, whose workflows downscale satellite pollutant rasters to the `GEO.DAT` grid, enforce coarse-to-fine conservation, validate against stations, and compare prepared CALPUFF rasters with satellite or downscaled reference rasters.

## 1. Scientific Problem

The central difficulty is that CALPUFF output is stated to be in **model arbitrary units**. Therefore, a direct comparison between raw CALPUFF values and satellite or station concentrations is not scientifically valid. A high raw spatial correlation may indicate a useful plume predictor, but it does not prove quantitative concentration accuracy. Conversely, a poor absolute bias before calibration may simply reflect an arbitrary scaling problem rather than a transport or dispersion failure.

The defensible validation target is therefore:

> CALPUFF is working well if its independently calibrated fields reproduce observed near-surface concentrations at withheld stations and reproduce the independent spatial patterns of downscaled satellite fields under documented time, unit, background, and representativeness assumptions.

This is a stronger and more defensible claim than saying that CALPUFF visually resembles a satellite map.

## 2. SmokEye Context

SmokEye provides three components that are directly relevant:

1. `downscale_pollutant.py` generates CALMET-grid pollutant rasters from coarser satellite input using deterministic, AI, or diffusion workflows. These methods are not simple resampling methods; they redistribute a coarse satellite value across finer CALMET cells using a dynamic weight field and conservative allocation.
2. `prepare_calpuff.py` reads gridded CALPUFF-style outputs, selects pollutant, source group, level, and time window, and applies explicit scale, offset, and background parameters.
3. `compare_calpuff_satellite.py` compares prepared CALPUFF and reference rasters pixel by pixel and writes difference, ratio, JSON, and CSV diagnostics.

The validation action proposed here adds a fourth layer: **station-based calibration and holdout validation** of CALPUFF arbitrary units before the CALPUFF-to-satellite comparison is interpreted.

## 3. Guiding Principles

### 3.1 Separate calibration from validation

The same station records must not be used both to estimate the CALPUFF scale/background and to claim final performance. At minimum, use station holdout validation. If there are enough data, use spatial, temporal, and episode-based cross-validation.

Recommended validation splits are:

- **site-based split**: train on a subset of stations, validate on withheld stations;
- **time-based split**: train on some days or episodes, validate on different days or episodes;
- **episode-based split**: fit on a subset of meteorological or emission episodes and validate on independent episodes;
- **blocked space-time split**: withhold station groups and time windows together to avoid optimistic estimates caused by spatial and temporal autocorrelation.

### 3.2 Use stations as the primary physical-unit reference

Stations measure near-surface concentrations and are the correct primary reference for calibrating CALPUFF arbitrary units into a physical unit such as `ug_m3`. Satellite products may represent tropospheric columns, aerosol optical properties, or retrieval-based surface estimates; therefore, their relationship with station concentrations is pollutant-specific and retrieval-specific.

### 3.3 Use downscaled satellite rasters as spatial-pattern evidence

After independent station calibration, downscaled satellite rasters are valuable for evaluating spatial structure that stations cannot fully observe. The key diagnostics are plume direction, hotspot displacement, spatial correlation, and percentile-threshold detection, not only mean bias.

### 3.4 Preserve all unit, time, and background assumptions

Every validation run must report:

- CALPUFF species, source group, level, and time window;
- satellite product and validity window;
- station observation averaging period;
- target unit;
- scale, offset, and background parameters;
- whether background was fixed, station-fitted, or externally supplied;
- station split used for calibration and validation;
- interpretation caveats.

## 4. Recommended Statistical Model

Let \(M_p(x,t)\) be the raw CALPUFF model-unit value for pollutant \(p\), and let \(C_p(x,t)\) be the target concentration in the validation unit.

The first-order calibration model should be:

\[
\hat{C}_p(x,t) = a_p M_p(x,t) + b_p + B_p(x,t),
\]

where:

- \(a_p\) is a pollutant-specific scale factor;
- \(b_p\) is an optional fitted offset;
- \(B_p(x,t)\) is a known or estimated background concentration field.

For the first operational SmokeEye implementation, a robust and auditable default is:

\[
\hat{C}_p(x,t) = a_p M_p(x,t) + B_p,
\]

with a fixed scalar background \(B_p\) and scale \(a_p\) fitted through the origin after background subtraction. This avoids overfitting when the number of stations is small. A fitted offset should be enabled only when there are enough independent stations and episodes.

## 5. Pollutant-Specific Strategy

### 5.1 NO2

NO2 should be the primary validation pollutant when the target sources are combustion-related. It has strong spatial gradients near roads, stacks, ports, and urban combustion sources, so it is useful for evaluating plume placement and local dispersion. However, satellite NO2 commonly represents tropospheric column information, whereas stations measure near-surface concentrations. The validation must therefore distinguish between column-pattern agreement and surface-concentration agreement.

Recommended NO2 evidence:

- station holdout RMSE, MAE, bias, and correlation after CALPUFF calibration;
- plume direction and hotspot overlap between calibrated CALPUFF and downscaled satellite NO2;
- stratification by wind direction, boundary-layer depth, and time of day;
- sensitivity to background assumptions.

### 5.2 CO

CO is useful for broader transport, background, and long-range pattern checks. It is often less locally reactive than NO2, so it may validate regional transport and episode timing better than near-source gradients. In many cases, CO spatial gradients are weaker, so hotspot metrics may be less discriminating than correlation and large-scale bias diagnostics.

Recommended CO evidence:

- stability of the fitted CALPUFF scale across episodes;
- agreement in regional plume displacement and timing;
- station holdout metrics after background correction;
- comparison against satellite/downscaled fields only after careful background treatment.

### 5.3 PM10

PM10 must be handled cautiously. Satellite-derived PM10 products are often inferred from aerosol optical depth or related aerosol observables and may require meteorology, humidity, vertical-profile, and aerosol-type assumptions. Ground PM10 stations remain the main quantitative reference. Satellite/downscaled PM10 is useful for broad spatial structure, dust or smoke plume extent, and event diagnostics, but station validation is essential.

Recommended PM10 evidence:

- station holdout skill as the primary proof;
- separate evaluation for dust, smoke, traffic, construction, and industrial episodes when metadata are available;
- robust metrics such as median absolute error and percentile/hotspot detection;
- caution when interpreting satellite-inferred PM10 as near-surface PM10.

## 6. Validation Workflow

### Step 1: Select episodes

Select episodes with:

- valid satellite coverage;
- concurrent CALMET/CALPUFF output;
- air-quality station data in the same averaging period;
- meaningful signal above background;
- documented cloud, retrieval-quality, and time-window filters.

Avoid cherry-picking only visually successful cases. Include different wind sectors, stability regimes, seasons, and source intensities.

### Step 2: Downscale satellite data with SmokEye

For each pollutant and time window, run `downscale_pollutant.py` using a documented method. Enable validation and station reporting where suitable:

```bash
python downscale_pollutant.py \
  satellite_NO2.tif \
  cmet.dat \
  GEO.DAT \
  outputs/no2_downscaled.tif \
  --pollutant NO2 \
  --satellite-time-start 2025-02-25T07:00:00 \
  --satellite-time-end 2025-02-25T08:00:00 \
  --groundtruth-csv stations_no2.csv \
  --groundtruth-value-column NO2 \
  --validate \
  --station-report outputs/no2_downscaled.station_report.json
```

The purpose is to establish that the downscaled satellite field is a credible reference product before using it to diagnose CALPUFF.

### Step 3: Prepare CALPUFF outputs

Use SmokEye's CALPUFF preparation workflow to select the relevant species, group, level, and time. If the scale is not yet known, prepare or export a raw model-unit raster for station calibration. After calibration, rerun preparation or apply the calibrated scale/background explicitly.

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
  --background 0.0 \
  --out-prefix outputs/no2_raw_for_validation
```

### Step 4: Calibrate CALPUFF arbitrary units with station training data

Fit the scale and optional offset only on training stations/times:

\[
a_p = \arg\min_a \sum_{i \in train} \left[C_i - B_p - a M_i\right]^2.
\]

Then evaluate on withheld stations/times. The new `smokeye-validation.py` entry point implements this station calibration and holdout validation.

### Step 5: Evaluate station holdout skill

Report at least:

- number of training and test stations;
- bias, MAE, RMSE, and correlation for training and test splits;
- median absolute error;
- scale, offset, and background;
- scatterplot-ready station CSV;
- sensitivity to station split if the network is small.

A successful result should show skill on withheld stations, not only a good fit at training stations.

### Step 6: Evaluate satellite spatial-pattern skill

After applying the station-derived calibration, compare the calibrated CALPUFF raster with the downscaled satellite raster:

```bash
python compare_calpuff_satellite.py \
  --model outputs/no2_validated.calpuff_calibrated.tif \
  --satellite outputs/no2_downscaled.tif \
  --out-prefix outputs/no2_calpuff_vs_satellite
```

Also compute pattern diagnostics:

- spatial correlation;
- bias and RMSE over valid pixels;
- hotspot precision, recall, and F1 above the 90th or 95th percentile;
- centroid or maximum-distance displacement when plume morphology is well defined;
- wind-sector stratification.

### Step 7: Repeat across pollutants and episodes

The final evidence should be a table across pollutant, episode, and validation split:

| Pollutant | Evidence role | Main success criterion |
|---|---|---|
| NO2 | Primary combustion/plume tracer | stable station skill and plume-pattern agreement |
| CO | Regional transport/background tracer | stable scale and broad spatial/temporal agreement |
| PM10 | Particle-event diagnostic | station skill plus event/hotspot agreement |

## 7. Minimum Acceptance Criteria

The following criteria are recommended before claiming that CALPUFF is working well:

1. The fitted scale is positive and reasonably stable across independent episodes.
2. Independent station validation has lower error than a simple baseline, such as a spatial mean, persistence, or background-only predictor.
3. The station-test bias is small relative to the observed mean or regulatory decision threshold.
4. Spatial correlation with downscaled satellite fields is positive and robust across episodes.
5. Hotspot detection has acceptable recall without excessive false alarms.
6. Results remain interpretable after stratifying by wind sector, stability, and time of day.
7. All failed or weak episodes are retained and explained rather than removed.

## 8. Recommended Repository Integration

Add the new script at repository root:

```text
SmokEye/
├── smokeye-validation.py
├── smokeye-validation-strategy.md
├── smokeye-validation-guide.md
├── downscale_pollutant.py
├── prepare_calpuff.py
├── compare_calpuff_satellite.py
└── smokeye/
```

Later, the script can be refactored into `smokeye/validation.py` and exposed through `smokeye.cli`, following the existing repository pattern in which top-level scripts are thin wrappers around package functions.

## 9. Interpretation Statement for Publications

A suitable publication-quality statement is:

> CALPUFF arbitrary-unit fields were transformed to pollutant concentrations using scale and background parameters estimated only from training station observations. Predictive skill was evaluated on independent station holdouts and subsequently diagnosed against SmokEye-downscaled satellite fields on the CALMET grid. Agreement was assessed using concentration error metrics, plume-pattern metrics, hotspot detection, and meteorological stratification, while all time-window, unit-conversion, background, and representativeness assumptions were retained in machine-readable validation reports.

## References

Berrocal, V. J., Gelfand, A. E., & Holland, D. M. (2010). A spatio-temporal downscaler for output from numerical models. *Journal of Agricultural, Biological, and Environmental Statistics, 15*(2), 176-197. https://doi.org/10.1007/s13253-009-0004-z

Berrocal, V. J., Gelfand, A. E., & Holland, D. M. (2012). Space-time data fusion under error in computer model output: An application to modeling air quality. *Biometrics, 68*(3), 837-848. https://doi.org/10.1111/j.1541-0420.2011.01725.x

Dresser, A. L., & Huizer, R. D. (2011). CALPUFF and AERMOD model validation study in the near field: Martins Creek revisited. *Journal of the Air & Waste Management Association, 61*(6), 647-659. https://doi.org/10.3155/1047-3289.61.6.647

Eicher, C. L., & Brewer, C. A. (2001). Dasymetric mapping and areal interpolation: Implementation and evaluation. *Cartography and Geographic Information Science, 28*(2), 125-138. https://doi.org/10.1559/152304001782173727

Ghannam, K., & El-Fadel, M. (2013). Emissions characterization and regulatory compliance at an industrial complex: An integrated MM5/CALPUFF approach. *Atmospheric Environment, 69*, 156-169. https://doi.org/10.1016/j.atmosenv.2012.12.022

Li, T., Shen, H., Yuan, Q., Zhang, X., & Zhang, L. (2019). Estimating ground-level PM2.5 by fusing satellite and station observations: A geo-intelligent deep learning approach. *Geophysical Research Letters, 46*(20), 11985-11993. https://doi.org/10.1002/2017GL075710

Mennis, J. (2003). Generating surface models of population using dasymetric mapping. *The Professional Geographer, 55*(1), 31-42. https://doi.org/10.1111/0033-0124.10042

Tobler, W. R. (1979). Smooth pycnophylactic interpolation for geographical regions. *Journal of the American Statistical Association, 74*(367), 519-530. https://doi.org/10.1080/01621459.1979.10481647

van Geffen, J., Boersma, K. F., Eskes, H., Sneep, M., ter Linden, M., Zara, M., & Veefkind, J. P. (2022). Sentinel-5P TROPOMI NO2 retrieval: Impact of version v2.2 improvements and comparisons with OMI and ground-based data. *Atmospheric Measurement Techniques, 15*, 2037-2060. https://doi.org/10.5194/amt-15-2037-2022

Zheng, Y., Zhang, Q., Liu, Y., Geng, G., & He, K. (2016). Estimating ground-level PM2.5 concentrations over three megalopolises in China using satellite-derived aerosol optical depth measurements. *Atmospheric Environment, 124*, 232-242. https://doi.org/10.1016/j.atmosenv.2015.06.046
