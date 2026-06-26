# Input Data Requirements

All downscaling methods use the same input files and command-line interface.

## Positional Inputs

```text
input_tif calmet_dat geodat output_tif
```

### `input_tif`

Input pollutant GeoTIFF. The command reads one 1-based band selected with `--input-band`.

Requirements:

- The raster must have a valid CRS.
- Pixel values must be numeric pollutant values.
- Pollutant concentrations are assumed to be in micrograms per cubic meter (`ug_m3`) unless `--pollutant-unit` documents a different already-prepared unit.
- Nodata values are skipped when present.

Example:

```bash
--input-band 1
```

### `calmet_dat`

CALMET/CMET meteorology file, or an `.npz` file if `--met-npz` is not supplied.

The binary reader looks for common Fortran-unformatted records:

```text
ZI, TEMPK, USTAR, Z0, U-LEV 1, V-LEV 1, ELEV, ILANDU
```

If the local CALMET layout differs, export meteorological fields to `.npz` and pass:

```bash
--met-npz path/to/met_fields.npz
```

Supported `.npz` field names include:

```text
pblh, ws10, u10, v10, ustar, tempk, z0, elevation_calmet, landuse_calmet
```

Each array must have shape `(ny, nx)` on the `GEO.DAT` grid. If `u10` and `v10` are present, `ws10` is derived automatically.

#### Array Orientation

CALMET/CMET binary grid records are read with:

```bash
--calmet-array-origin lower
```

This default preserves historical behavior: source records are assumed to be stored south-to-north and are flipped into north-to-south raster row order. If the CALMET producer already writes records in raster order, use:

```bash
--calmet-array-origin upper
```

The `.npz` path is always treated as already prepared in raster row order `(row 0 = north/top)`. Prepare `.npz` files with the same CRS, transform, and orientation as the `GEO.DAT` target grid.

#### Time Selection

CALMET/CMET files may contain multiple gridded records for the same variable at different model timestamps. SmokEye reads supported records and selects one array per meteorological field.

Available selector modes are:

```bash
--calmet-selector first
--calmet-selector last
--calmet-selector mean
```

For normal downscaling, SmokEye uses the pollutant raster timestamp to derive a target CALMET stamp in the selected or inferred stamp format and selects the closest available weather record for each field. Use `first` or `last` only with `--allow-untimed-satellite` or already trimmed CALMET files where record order is the documented time-selection rule. Use `mean` when the pollutant raster represents a time average and the meteorological influence should also be averaged over the available records.

If the CALMET integer timestamp for the desired analysis time is known, select the nearest record with:

```bash
--calmet-stamp 2024062811
```

CALMET/CMET files commonly use one of two timestamp encodings:

```bash
--calmet-stamp-format auto
--calmet-stamp-format yyyymmddhh
--calmet-stamp-format yyyydddhhh
```

`auto` is the default and infers the encoding from available nonzero CALMET stamps. `yyyymmddhh` represents calendar stamps such as `2024062811`. `yyyydddhhh` represents year, Julian day, and an hour field; for example, `202418011` decodes to `2024-06-28T11:00:00`. SmokEye accepts both the observed 9-digit form and 10-digit elapsed-hour variants. Use `--inspect-calmet` to list raw stamps, decoded datetimes, and the inferred format.

The integer is interpreted as a CALMET record stamp in the selected format. SmokEye does not infer time zones from CALMET stamps and does not compare them with dates embedded in filenames.

Use `--max-calmet-stamp-delta` to limit how far, in hours, the nearest available CALMET stamp may be from the requested or satellite-derived stamp. Static CALMET fields with stamp `0`, such as roughness, land use, elevation, and LAI records, are accepted as static metadata rather than rejected as time mismatches. Untimed pollutant rasters fail by default; use `--allow-untimed-satellite` only for a documented diagnostic or preselected input package.

For `.npz` meteorology, there is no internal time selection. The arrays are assumed to have already been selected or averaged for the intended pollutant analysis time.

### `geodat`

CALMET `GEO.DAT` file used to infer the target grid.

The reader attempts to infer:

- CRS, currently with UTM/WGS-84 support.
- `nx`, `ny`.
- `x0`, `y0`.
- `dx`, `dy`.
- origin.
- optional terrain.
- optional land-use.

Embedded terrain and land-use arrays are read with:

```bash
--geodat-array-origin lower
```

This default means the values are stored from the lower/southern row upward and must be flipped into GeoTIFF row order. If the `GEO.DAT` arrays are already north-to-south, use:

```bash
--geodat-array-origin upper
```

Use `--write-weight` and a strict run (`--no-seamless --deblock-sigma-m 0`) to diagnose orientation issues. A vertically mirrored high-resolution pattern in the weight raster usually means `--geodat-array-origin`, `--calmet-array-origin`, or both need to be changed.

If a local `GEO.DAT` variant cannot be inferred automatically, create a JSON sidecar:

```json
{
  "crs": "EPSG:32633",
  "nx": 100,
  "ny": 100,
  "x0": 434304.0,
  "y0": 4515091.0,
  "dx": 200.0,
  "dy": 200.0,
  "origin": "lower-left",
  "array_origin": "lower"
}
```

Then pass:

```bash
--geodat-sidecar geodat_grid.json
```

### `output_tif`

Output single-band GeoTIFF. Parent directories are created automatically by the writer.

## Optional Station CSV

Station correction uses:

```bash
--groundtruth-csv path/to/groundtruth.csv
```

The CSV must contain station ID and coordinates:

```csv
ID,LAT,LON,NO2
AQSTN_A1,40.814289,14.267230,9.9736753e-05
AQSTN_B2,40.845249,14.321457,0.00015246817
```

Column matching is case-insensitive. For pollutants other than `NO2`, either name the value column after `--pollutant` or specify it explicitly:

```bash
--groundtruth-value-column PM25
```

`PM25` also accepts `PM2.5` when present.

Station CSV files do not carry a time axis in the SmokEye workflow. If source station data are hourly or sub-hourly, prepare the CSV before running SmokEye so each station row contains the measurement or average corresponding to the pollutant raster and selected CALMET period.

## Inspection Commands

Inspect the target grid:

```bash
python downscale_pollutant.py --inspect-geodat data/geo.dat
```

Inspect CALMET records:

```bash
python downscale_pollutant.py --inspect-calmet data/cmet.dat
```

Inspect station CSV and estimate background:

```bash
python downscale_pollutant.py \
  --pollutant NO2 \
  --inspect-groundtruth examples/groundtruth_example.csv
```

The AI method supports the same inspection commands by adding `--method ai`.
