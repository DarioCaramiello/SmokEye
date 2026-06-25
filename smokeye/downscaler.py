#!/usr/bin/env python3
"""
Dynamic conservative downscaling of gridded satellite or model pollutant fields to a
CALMET GEO.DAT grid, with optional CALMET meteorology and station ground-truth
correction.

The script is pollutant-agnostic. It can be used for NO2, O3, PM10, PM2.5/PM25,
SO2, CO, or any scalar pollutant field available as a raster band.

Main idea:
  * Read a selected band from the input pollutant GeoTIFF.
  * Infer the target grid from CALMET GEO.DAT, with optional JSON sidecar fallback.
  * Read terrain and land-use from GEO.DAT when present.
  * Read useful meteorology from CALMET/CMET.DAT Fortran-unformatted binary records,
    or from an NPZ file if supplied.
  * Build a dynamic fine-grid weight field on the GEO.DAT grid.
  * Conservatively redistribute each coarse input pixel value to overlapping fine cells.
  * Optionally use air-quality stations to correct the dynamic weights.
  * Estimate and report an average background pollutant value from station data.
  * Optionally apply seamless/deblocking regularization to reduce visible coarse-pixel seams.
  * Write a single-band GeoTIFF in the GEO.DAT CRS/resolution/reference.

The output is not native high-resolution satellite information. It is a
model-assisted fine-grid allocation of the original gridded pollutant field.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import struct
import warnings
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.transform import Affine, from_origin
from shapely.geometry import box
from shapely.ops import transform as shapely_transform
from scipy.ndimage import gaussian_filter


@dataclass(frozen=True)
class GeoGrid:
    crs: CRS
    nx: int
    ny: int
    x0: float
    y0: float
    dx: float
    dy: float
    origin: str = "lower-left"
    elevation: Optional[np.ndarray] = None
    landuse: Optional[np.ndarray] = None

    @property
    def transform(self) -> Affine:
        # Raster rows are north-to-south. GEO.DAT origin is normally lower-left.
        if self.origin.lower() == "lower-left":
            return from_origin(self.x0, self.y0 + self.ny * self.dy, self.dx, self.dy)
        if self.origin.lower() == "upper-left":
            return from_origin(self.x0, self.y0, self.dx, self.dy)
        raise ValueError(f"Unsupported grid origin: {self.origin}")

    @property
    def bounds(self) -> Tuple[float, float, float, float]:
        if self.origin.lower() == "lower-left":
            return (self.x0, self.y0, self.x0 + self.nx * self.dx, self.y0 + self.ny * self.dy)
        return (self.x0, self.y0 - self.ny * self.dy, self.x0 + self.nx * self.dx, self.y0)

    def as_dict(self) -> dict:
        xmin, ymin, xmax, ymax = self.bounds
        return {
            "crs": self.crs.to_string(),
            "nx": self.nx,
            "ny": self.ny,
            "dx": self.dx,
            "dy": self.dy,
            "origin": self.origin,
            "x0": self.x0,
            "y0": self.y0,
            "bounds": {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax},
            "has_elevation": self.elevation is not None,
            "has_landuse": self.landuse is not None,
        }




@dataclass(frozen=True)
class GroundTruthStations:
    ids: List[str]
    lat: np.ndarray
    lon: np.ndarray
    value: np.ndarray
    x: Optional[np.ndarray] = None
    y: Optional[np.ndarray] = None

    def with_xy(self, crs: CRS) -> "GroundTruthStations":
        transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        x, y = transformer.transform(self.lon, self.lat)
        return GroundTruthStations(
            ids=self.ids,
            lat=self.lat,
            lon=self.lon,
            value=self.value,
            x=np.asarray(x, dtype=float),
            y=np.asarray(y, dtype=float),
        )

    def as_summary(self) -> dict:
        return {
            "n": len(self.ids),
            "value_min": float(np.nanmin(self.value)) if len(self.ids) else None,
            "value_max": float(np.nanmax(self.value)) if len(self.ids) else None,
            "value_mean": float(np.nanmean(self.value)) if len(self.ids) else None,
            "value_median": float(np.nanmedian(self.value)) if len(self.ids) else None,
        }


@dataclass(frozen=True)
class RasterReference:
    crs: CRS
    transform: Affine
    width: int
    height: int
    nodata: Optional[float] = None


class GeoDATReader:
    def __init__(self, geodat: Path, sidecar: Optional[Path] = None):
        self.geodat = Path(geodat)
        self.sidecar = Path(sidecar) if sidecar else None

    def read(self) -> GeoGrid:
        if self.sidecar:
            return self._read_sidecar(self.sidecar)
        text = self.geodat.read_text(errors="ignore")
        lines = text.splitlines()
        grid = self._infer_grid_from_header(lines)
        landuse = self._read_landuse(lines, grid.nx, grid.ny)
        elevation = self._read_elevation(lines, grid.nx, grid.ny)
        return GeoGrid(
            crs=grid.crs,
            nx=grid.nx,
            ny=grid.ny,
            x0=grid.x0,
            y0=grid.y0,
            dx=grid.dx,
            dy=grid.dy,
            origin=grid.origin,
            elevation=elevation,
            landuse=landuse,
        )

    @staticmethod
    def _read_sidecar(sidecar: Path) -> GeoGrid:
        cfg = json.loads(sidecar.read_text())
        return GeoGrid(
            crs=CRS.from_user_input(cfg["crs"]),
            nx=int(cfg["nx"]),
            ny=int(cfg["ny"]),
            x0=float(cfg["x0"]),
            y0=float(cfg["y0"]),
            dx=float(cfg["dx"]),
            dy=float(cfg["dy"]),
            origin=cfg.get("origin", "lower-left"),
        )

    @staticmethod
    def _infer_grid_from_header(lines: List[str]) -> GeoGrid:
        # The CALMET GEO.DAT header commonly contains:
        #   UTM
        #    33N
        #   WGS-84 ...
        #      100     100     434.304    4515.091       0.200       0.200
        #   KM  M
        projection = "UTM"
        zone = None
        datum = "WGS-84"
        unit_line = "KM M"
        grid_line = None

        for i, line in enumerate(lines[:80]):
            if re.search(r"\bUTM\b", line.upper()):
                projection = "UTM"
                # Try the next few lines for zone like 33N.
                for j in range(i + 1, min(i + 5, len(lines))):
                    m = re.search(r"\b(\d{1,2})([NS])\b", lines[j].upper())
                    if m:
                        zone = int(m.group(1))
                        hemi = m.group(2)
                        break
            if "WGS" in line.upper():
                datum = "WGS-84"

        # Find the first numeric line that looks like nx ny x0 y0 dx dy.
        for i, line in enumerate(lines[:120]):
            nums = re.findall(r"[-+]?\d+(?:\.\d+)?", line)
            if len(nums) >= 6:
                try:
                    nx = int(float(nums[0]))
                    ny = int(float(nums[1]))
                    x0 = float(nums[2])
                    y0 = float(nums[3])
                    dx = float(nums[4])
                    dy = float(nums[5])
                except ValueError:
                    continue
                if 1 <= nx <= 10000 and 1 <= ny <= 10000 and dx > 0 and dy > 0:
                    grid_line = i
                    break
        else:
            raise ValueError("Could not infer nx, ny, x0, y0, dx, dy from GEO.DAT header")

        if grid_line + 1 < len(lines):
            unit_line = lines[grid_line + 1].upper()
        xy_factor = 1000.0 if "KM" in unit_line else 1.0
        d_factor = 1000.0 if re.search(r"\bKM\b", unit_line) else 1.0

        # For line 'KM  M', CALMET x0/y0/dx/dy are in km and heights in m.
        x0 *= xy_factor
        y0 *= xy_factor
        dx *= d_factor
        dy *= d_factor

        if projection.upper() == "UTM" and zone:
            epsg = 32600 + zone if hemi == "N" else 32700 + zone
            crs = CRS.from_epsg(epsg)
        else:
            crs = CRS.from_epsg(32633)

        return GeoGrid(crs=crs, nx=nx, ny=ny, x0=x0, y0=y0, dx=dx, dy=dy)

    @staticmethod
    def _numbers_after(lines: List[str], start: int, want: int, kind: str) -> Optional[np.ndarray]:
        vals: List[float] = []
        for line in lines[start:]:
            # Stop if a new obvious section starts after collecting some values.
            if vals and re.search(r"-\s*[A-Z0-9 /]+\s*-", line):
                break
            vals.extend(float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?", line))
            if len(vals) >= want:
                arr = np.array(vals[:want], dtype=float)
                return arr
        return None

    @classmethod
    def _read_landuse(cls, lines: List[str], nx: int, ny: int) -> Optional[np.ndarray]:
        want = nx * ny
        for i, line in enumerate(lines):
            if "LAND USE DATA" in line.upper():
                # Skip section title, NLU line, category list line; data starts after that.
                arr = cls._numbers_after(lines, i + 3, want, "int")
                if arr is not None:
                    return np.flipud(arr.reshape((ny, nx)).astype(np.int16))
        return None

    @classmethod
    def _read_elevation(cls, lines: List[str], nx: int, ny: int) -> Optional[np.ndarray]:
        want = nx * ny
        for i, line in enumerate(lines):
            if "TERRAIN" in line.upper() and ("HEIGHT" in line.upper() or "ELEV" in line.upper()):
                factor = 1.0
                nums = re.findall(r"[-+]?\d+(?:\.\d+)?", line)
                if nums:
                    # First number on the line is HTFAC in this GEO.DAT.
                    try:
                        factor = float(nums[0])
                    except ValueError:
                        factor = 1.0
                arr = cls._numbers_after(lines, i + 1, want, "float")
                if arr is not None:
                    return np.flipud((arr * factor).reshape((ny, nx)).astype(np.float32))
        return None


class MetReader:
    def __init__(
        self,
        path: Optional[Path],
        grid: GeoGrid,
        met_npz: Optional[Path] = None,
        selector: str = "last",
        stamp: Optional[int] = None,
        allow_no_met: bool = False,
    ):
        self.path = Path(path) if path else None
        self.grid = grid
        self.met_npz = Path(met_npz) if met_npz else None
        self.selector = selector
        self.stamp = stamp
        self.allow_no_met = allow_no_met

    def read(self) -> Dict[str, np.ndarray]:
        try:
            if self.met_npz:
                return self._read_npz(self.met_npz)
            if self.path and self.path.suffix.lower() == ".npz":
                return self._read_npz(self.path)
            if self.path and self.path.exists():
                return self._read_calmet(self.path)
            if self.allow_no_met:
                return {}
            raise FileNotFoundError(self.path)
        except Exception as exc:
            if self.allow_no_met:
                print(f"WARNING: meteorology ignored: {exc}")
                return {}
            raise

    def _read_npz(self, path: Path) -> Dict[str, np.ndarray]:
        z = np.load(path)
        met = {}
        for name in z.files:
            arr = np.asarray(z[name], dtype=float)
            if arr.shape != (self.grid.ny, self.grid.nx):
                raise ValueError(f"NPZ field {name} has shape {arr.shape}; expected {(self.grid.ny, self.grid.nx)}")
            met[name.lower()] = arr
        if "ws10" not in met and "u10" in met and "v10" in met:
            met["ws10"] = np.sqrt(met["u10"] ** 2 + met["v10"] ** 2)
        return met

    def _select(self, records: List[Tuple[int, np.ndarray]]) -> Optional[np.ndarray]:
        if not records:
            return None
        if self.stamp is not None:
            return min(records, key=lambda r: abs(r[0] - self.stamp))[1]
        if self.selector == "first":
            return records[0][1]
        if self.selector == "mean":
            return np.nanmean([r[1] for r in records], axis=0)
        return records[-1][1]

    def _read_calmet(self, path: Path) -> Dict[str, np.ndarray]:
        nx, ny = self.grid.nx, self.grid.ny
        n = nx * ny
        fields: Dict[str, List[Tuple[int, np.ndarray]]] = defaultdict(list)
        endian = self._detect_fortran_endian(path)

        with path.open("rb") as f:
            rec_idx = 0
            while True:
                head = f.read(4)
                if len(head) < 4:
                    break
                reclen = struct.unpack(endian + "i", head)[0]
                if reclen <= 0 or reclen > 200_000_000:
                    break
                payload = f.read(reclen)
                tail = f.read(4)
                if len(payload) != reclen or len(tail) != 4:
                    break
                rec_idx += 1

                # CALMET gridded fields are usually: 8-byte label, 4-byte time stamp, nx*ny f4 values.
                if reclen < 12 + 4 * n:
                    continue
                label = payload[:8].decode("ascii", errors="ignore").strip().upper()
                if not label:
                    continue
                try:
                    stamp = struct.unpack(endian + "i", payload[8:12])[0]
                    arr = np.frombuffer(payload[12:12 + 4 * n], dtype=endian + "f4", count=n).astype(float)
                except Exception:
                    continue
                if arr.size != n:
                    continue
                arr = np.flipud(arr.reshape((ny, nx)))
                fields[label].append((stamp, arr))

        met: Dict[str, np.ndarray] = {}
        direct = {
            "ZI": "pblh",
            "TEMPK": "tempk",
            "USTAR": "ustar",
            "Z0": "z0",
            "ELEV": "elevation_calmet",
            "ILANDU": "landuse_calmet",
        }
        for key, out_name in direct.items():
            arr = self._select(fields.get(key, []))
            if arr is not None:
                met[out_name] = arr

        def collect(prefix: str) -> List[Tuple[int, np.ndarray]]:
            out: List[Tuple[int, np.ndarray]] = []
            for key, records in fields.items():
                compact = key.replace(" ", "")
                if compact.startswith(prefix):
                    out.extend(records)
            return out

        u = self._select(collect("U-LEV1") or fields.get("U-LEV  1", []))
        v = self._select(collect("V-LEV1") or fields.get("V-LEV  1", []))
        if u is not None:
            met["u10"] = u
        if v is not None:
            met["v10"] = v
        if u is not None and v is not None:
            met["ws10"] = np.sqrt(u * u + v * v)

        if not met:
            labels = sorted(fields.keys())[:30]
            raise ValueError(f"No supported CALMET gridded fields found. First labels: {labels}")
        return met

    @staticmethod
    def _detect_fortran_endian(path: Path) -> str:
        with path.open("rb") as f:
            h = f.read(4)
        if len(h) != 4:
            raise ValueError(f"Empty or invalid CALMET file: {path}")
        le = struct.unpack("<i", h)[0]
        be = struct.unpack(">i", h)[0]
        if 0 < le < 10_000_000:
            return "<"
        if 0 < be < 10_000_000:
            return ">"
        raise ValueError("Could not determine Fortran record endian/order")

    @staticmethod
    def inspect(path: Path, limit: int = 80) -> List[dict]:
        endian = MetReader._detect_fortran_endian(path)
        rows = []
        with path.open("rb") as f:
            i = 0
            while len(rows) < limit:
                head = f.read(4)
                if len(head) < 4:
                    break
                reclen = struct.unpack(endian + "i", head)[0]
                if reclen <= 0 or reclen > 200_000_000:
                    break
                payload = f.read(reclen)
                tail = f.read(4)
                if len(payload) != reclen or len(tail) != 4:
                    break
                label = payload[:8].decode("ascii", errors="ignore").strip()
                if reclen >= 1000 or label in {"ZI", "TEMPK", "USTAR"} or label.startswith(("U-LEV", "V-LEV")):
                    rows.append({"record": i, "length": reclen, "label": label})
                i += 1
        return rows


def robust01(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=float)
    finite = np.isfinite(arr)
    out = np.zeros_like(arr, dtype=float)
    if not finite.any():
        return out
    lo, hi = np.nanpercentile(arr[finite], [2, 98])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return np.ones_like(arr, dtype=float)
    out = (arr - lo) / (hi - lo)
    return np.clip(out, 0.0, 1.0)


def build_weights(grid: GeoGrid, met: Dict[str, np.ndarray], min_weight: float = 0.05) -> np.ndarray:
    w = np.ones((grid.ny, grid.nx), dtype=float)

    # Land-use/activity proxy. These class factors are intentionally conservative and editable.
    if grid.landuse is not None:
        lu = grid.landuse.astype(int)
        factors = np.ones_like(w)
        # Common CALMET/USGS-like categories in this file: 10,20,30,40,51,55,70.
        # Give more weight to urban/built/transport-like and less to water/vegetation.
        lut = {
            10: 1.50,  # urban or dryland/cropland depending category table; modest enhancement
            20: 1.20,
            30: 1.10,
            40: 0.90,
            50: 0.45,
            51: 0.45,
            54: 0.45,
            55: 0.45,
            60: 0.80,
            61: 0.80,
            62: 0.80,
            70: 0.70,
            80: 0.70,
            90: 0.70,
        }
        for k, v in lut.items():
            factors[lu == k] = v
        w *= factors

    # Terrain effect: slight valley/lowland enhancement only, never dominant.
    elev = grid.elevation
    if elev is None and "elevation_calmet" in met:
        elev = met["elevation_calmet"]
    if elev is not None:
        lowland = 1.0 - robust01(elev)
        w *= 0.85 + 0.30 * lowland

    # Meteorological modifiers.
    if "pblh" in met:
        pblh = np.maximum(np.asarray(met["pblh"], dtype=float), 50.0)
        inv = np.nanmedian(pblh) / pblh
        w *= np.clip(inv, 0.4, 2.5)

    if "ws10" in met:
        ws = np.maximum(np.asarray(met["ws10"], dtype=float), 0.05)
        # Calm/stagnant conditions increase local accumulation; windy cells decrease it.
        calm = np.nanmedian(ws) / ws
        w *= np.clip(calm, 0.5, 2.0)

    if "ustar" in met:
        ustar = np.maximum(np.asarray(met["ustar"], dtype=float), 0.02)
        turb = np.nanmedian(ustar) / ustar
        w *= np.clip(turb, 0.7, 1.5)

    w = np.where(np.isfinite(w), w, 1.0)
    return np.maximum(w, min_weight)


def cell_polygon(transform: Affine, row: int, col: int):
    x_left, y_top = transform * (col, row)
    x_right, y_bottom = transform * (col + 1, row + 1)
    xmin, xmax = sorted((x_left, x_right))
    ymin, ymax = sorted((y_bottom, y_top))
    return box(xmin, ymin, xmax, ymax)




def read_groundtruth_csv(path: Path, value_column: str = "POLLUTANT") -> GroundTruthStations:
    """Read station CSV with ID,LAT,LON,<value_column>. Supports comma, semicolon, or tab delimiters.

    The value column can be POLLUTANT, NO2, O3, PM10, PM25, SO2, CO, or any user-supplied name.
    Column matching is case-insensitive.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if sample.count(";") > sample.count(",") else ","

    rows = list(csv.DictReader(text.splitlines(), dialect=dialect))
    if not rows:
        raise ValueError(f"Ground-truth CSV is empty: {path}")

    def col(name: str) -> str:
        lookup = {c.strip().upper(): c for c in rows[0].keys() if c is not None}
        if name not in lookup:
            raise ValueError(f"Ground-truth CSV must contain column {name}. Found: {list(rows[0].keys())}")
        return lookup[name]

    c_id, c_lat, c_lon = col("ID"), col("LAT"), col("LON")
    lookup = {c.strip().upper(): c for c in rows[0].keys() if c is not None}
    vc = value_column.strip().upper()
    if vc not in lookup and vc == "PM25" and "PM2.5" in lookup:
        vc = "PM2.5"
    if vc not in lookup:
        # Last fallback for legacy files generated for this project.
        if "NO2" in lookup:
            vc = "NO2"
        else:
            raise ValueError(f"Ground-truth CSV must contain value column {value_column}. Found: {list(rows[0].keys())}")
    c_value = lookup[vc]
    ids: List[str] = []
    lat: List[float] = []
    lon: List[float] = []
    value: List[float] = []
    for r in rows:
        try:
            ids.append(str(r[c_id]).strip())
            lat.append(float(str(r[c_lat]).replace(",", ".")))
            lon.append(float(str(r[c_lon]).replace(",", ".")))
            value.append(float(str(r[c_value]).replace(",", ".")))
        except Exception as exc:
            raise ValueError(f"Invalid ground-truth row: {r}") from exc
    return GroundTruthStations(ids=ids, lat=np.asarray(lat), lon=np.asarray(lon), value=np.asarray(value, dtype=float))


def estimate_background(value: np.ndarray, mode: str = "low-percentile", percentile: float = 40.0) -> float:
    vals = np.asarray(value, dtype=float)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return float("nan")
    if mode == "mean":
        return float(np.mean(vals))
    if mode == "median":
        return float(np.median(vals))
    if mode == "min":
        return float(np.min(vals))
    if mode == "none":
        return 0.0
    if mode == "low-percentile":
        q = np.nanpercentile(vals, percentile)
        low = vals[vals <= q]
        if low.size == 0:
            return float(q)
        return float(np.mean(low))
    raise ValueError(f"Unsupported background mode: {mode}")


def sample_grid_values(arr: np.ndarray, grid: GeoGrid, x: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    inv = ~grid.transform
    cols, rows = inv * (x, y)
    rows_i = np.floor(rows).astype(int)
    cols_i = np.floor(cols).astype(int)
    inside = (rows_i >= 0) & (rows_i < grid.ny) & (cols_i >= 0) & (cols_i < grid.nx)
    vals = np.full(len(x), np.nan, dtype=float)
    vals[inside] = arr[rows_i[inside], cols_i[inside]]
    return vals, rows_i, cols_i


def station_metrics(obs: np.ndarray, pred: np.ndarray) -> dict:
    obs = np.asarray(obs, dtype=float)
    pred = np.asarray(pred, dtype=float)
    mask = np.isfinite(obs) & np.isfinite(pred)
    if not mask.any():
        return {"n": 0}
    d = pred[mask] - obs[mask]
    out = {
        "n": int(mask.sum()),
        "obs_mean": float(np.mean(obs[mask])),
        "pred_mean": float(np.mean(pred[mask])),
        "bias_pred_minus_obs": float(np.mean(d)),
        "mae": float(np.mean(np.abs(d))),
        "rmse": float(np.sqrt(np.mean(d ** 2))),
    }
    if mask.sum() >= 2 and np.std(obs[mask]) > 0 and np.std(pred[mask]) > 0:
        out["corr"] = float(np.corrcoef(obs[mask], pred[mask])[0, 1])
    return out


def build_station_correction(
    grid: GeoGrid,
    base_field: np.ndarray,
    stations: GroundTruthStations,
    background_value: float,
    alpha: float = 0.65,
    power: float = 2.0,
    radius_m: float = 6000.0,
    ratio_min: float = 0.25,
    ratio_max: float = 4.0,
    use_background_excess: bool = True,
) -> Tuple[np.ndarray, dict]:
    """
    Build a smooth multiplicative correction for the dynamic weight field.

    The correction is applied to weights, then conservative downscaling is rerun.
    This means stations improve the sub-pixel pattern while the satellite coarse values
    remain conserved.
    """
    if stations.x is None or stations.y is None:
        stations = stations.with_xy(grid.crs)
    pred, _, _ = sample_grid_values(base_field, grid, stations.x, stations.y)
    obs = np.asarray(stations.value, dtype=float)
    valid = np.isfinite(obs) & np.isfinite(pred) & (pred > 0)
    if not valid.any():
        return np.ones((grid.ny, grid.nx), dtype=float), {"n_used": 0, "reason": "no valid station/model overlaps"}

    eps = max(float(np.nanmedian(pred[valid])) * 1.0e-3, 1.0e-12)
    if use_background_excess and np.isfinite(background_value):
        obs_excess = np.maximum(obs[valid] - background_value, eps)
        pred_excess = np.maximum(pred[valid] - background_value, eps)
        ratios = obs_excess / pred_excess
        ratio_mode = "background_excess"
    else:
        ratios = obs[valid] / np.maximum(pred[valid], eps)
        ratio_mode = "direct"
    ratios = np.clip(ratios, ratio_min, ratio_max)
    log_ratios = np.log(ratios)

    xs = stations.x[valid]
    ys = stations.y[valid]
    # Cell-center coordinates.
    cols = np.arange(grid.nx, dtype=float) + 0.5
    rows = np.arange(grid.ny, dtype=float) + 0.5
    xx, yy = grid.transform * np.meshgrid(cols, rows)
    xx = np.asarray(xx, dtype=float)
    yy = np.asarray(yy, dtype=float)

    num = np.zeros((grid.ny, grid.nx), dtype=float)
    den = np.zeros((grid.ny, grid.nx), dtype=float)
    for sx, sy, lr in zip(xs, ys, log_ratios):
        dist = np.sqrt((xx - sx) ** 2 + (yy - sy) ** 2)
        if radius_m > 0:
            influence = dist <= radius_m
        else:
            influence = np.ones_like(dist, dtype=bool)
        # Exact or very close station cell.
        dist = np.maximum(dist, 1.0)
        ww = np.zeros_like(dist, dtype=float)
        ww[influence] = 1.0 / (dist[influence] ** power)
        num += ww * lr
        den += ww

    corr = np.ones((grid.ny, grid.nx), dtype=float)
    mask = den > 0
    corr[mask] = np.exp(alpha * num[mask] / den[mask])
    corr = np.clip(corr, ratio_min, ratio_max)
    info = {
        "n_used": int(valid.sum()),
        "ids_used": [stations.ids[i] for i in np.where(valid)[0]],
        "background_value": float(background_value),
        "ratio_mode": ratio_mode,
        "raw_ratios": {stations.ids[i]: float(r) for i, r in zip(np.where(valid)[0], ratios)},
        "correction_min": float(np.nanmin(corr)),
        "correction_max": float(np.nanmax(corr)),
        "correction_mean": float(np.nanmean(corr)),
        "alpha": float(alpha),
        "radius_m": float(radius_m),
        "power": float(power),
    }
    return corr, info


def conservative_downscale_array(s5p_path: Path, grid: GeoGrid, weights: np.ndarray, band: int = 1) -> Tuple[np.ndarray, RasterReference, np.ndarray]:
    if weights.shape != (grid.ny, grid.nx):
        raise ValueError(f"Weights have shape {weights.shape}; expected {(grid.ny, grid.nx)}")

    out_sum = np.zeros((grid.ny, grid.nx), dtype=np.float64)
    out_area = np.zeros((grid.ny, grid.nx), dtype=np.float64)
    fine_transform = grid.transform

    with rasterio.open(s5p_path) as src:
        if band < 1 or band > src.count:
            raise ValueError(f"Input band {band} is outside available band range 1..{src.count}")
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Setting the shape on a NumPy array has been deprecated.*",
                category=DeprecationWarning,
            )
            input_band_array = src.read(band, out_dtype="float64")
        nodata = src.nodata
        src_crs = src.crs
        if src_crs is None:
            raise ValueError("Input satellite GeoTIFF has no CRS")
        ref = RasterReference(
            crs=CRS.from_user_input(src_crs),
            transform=src.transform,
            width=src.width,
            height=src.height,
            nodata=float(nodata) if nodata is not None else None,
        )
        transformer = Transformer.from_crs(src_crs, grid.crs, always_xy=True).transform
        inv_transform = ~fine_transform

        for row in range(src.height):
            for col in range(src.width):
                value = input_band_array[row, col]
                if not np.isfinite(value):
                    continue
                if nodata is not None and value == nodata:
                    continue

                x0, y0 = src.transform * (col, row)
                x1, y1 = src.transform * (col + 1, row + 1)
                poly_src = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                poly = shapely_transform(transformer, poly_src)
                if poly.is_empty:
                    continue

                minx, miny, maxx, maxy = poly.bounds
                c0, r0 = inv_transform * (minx, maxy)
                c1, r1 = inv_transform * (maxx, miny)
                rmin = max(0, int(math.floor(min(r0, r1))) - 1)
                rmax = min(grid.ny - 1, int(math.ceil(max(r0, r1))) + 1)
                cmin = max(0, int(math.floor(min(c0, c1))) - 1)
                cmax = min(grid.nx - 1, int(math.ceil(max(c0, c1))) + 1)
                if rmax < rmin or cmax < cmin:
                    continue

                overlaps: List[Tuple[int, int, float, float]] = []
                denom = 0.0
                area_total = 0.0
                for rr in range(rmin, rmax + 1):
                    for cc in range(cmin, cmax + 1):
                        cp = cell_polygon(fine_transform, rr, cc)
                        inter = poly.intersection(cp)
                        if inter.is_empty:
                            continue
                        area = inter.area
                        if area <= 0:
                            continue
                        wi = float(weights[rr, cc])
                        overlaps.append((rr, cc, area, wi))
                        denom += area * wi
                        area_total += area
                if denom <= 0 or area_total <= 0:
                    continue
                for rr, cc, area, wi in overlaps:
                    out_sum[rr, cc] += value * wi * area_total / denom * area
                    out_area[rr, cc] += area

    out = np.full((grid.ny, grid.nx), np.nan, dtype=np.float32)
    mask = out_area > 0
    out[mask] = (out_sum[mask] / out_area[mask]).astype(np.float32)
    return out, ref, input_band_array


def nan_gaussian_filter(arr: np.ndarray, sigma_cells: float) -> np.ndarray:
    """Gaussian smoothing that ignores NaN cells."""
    if sigma_cells <= 0:
        return arr.copy()
    a = np.asarray(arr, dtype=np.float64)
    valid = np.isfinite(a)
    if not np.any(valid):
        return arr.copy()
    filled = np.where(valid, a, 0.0)
    num = gaussian_filter(filled, sigma=sigma_cells, mode="nearest")
    den = gaussian_filter(valid.astype(np.float64), sigma=sigma_cells, mode="nearest")
    out = np.full_like(a, np.nan, dtype=np.float64)
    ok = den > 1.0e-12
    out[ok] = num[ok] / den[ok]
    out[~valid] = np.nan
    return out.astype(np.float32)


def deblock_fine_field(
    fine: np.ndarray,
    grid: GeoGrid,
    sigma_m: float = 400.0,
    strength: float = 0.75,
    iterations: int = 1,
    preserve_mean: bool = True,
) -> np.ndarray:
    """
    Reduce visible coarse-pixel seams in the fine output.

    This is a visual/regularization step applied after dynamic conservative allocation.
    It smooths only at the fine-grid scale using a NaN-aware Gaussian filter and blends
    the result with the original fine field. If preserve_mean is true, the domain mean
    over valid cells is restored after each iteration. Per-satellite-pixel hard conservation is
    intentionally not re-applied here, because hard per-pixel constraints are exactly what
    makes coarse pixel boundaries visible.
    """
    if sigma_m <= 0 or strength <= 0 or iterations <= 0:
        return fine
    sigma_cells = float(sigma_m) / float(max(grid.dx, grid.dy))
    if sigma_cells <= 0:
        return fine
    strength = min(1.0, max(0.0, float(strength)))
    out = fine.astype(np.float32, copy=True)
    valid = np.isfinite(out)
    if not np.any(valid):
        return out
    target_mean = float(np.nanmean(out[valid]))
    for _ in range(int(iterations)):
        smoothed = nan_gaussian_filter(out, sigma_cells)
        out = np.where(valid, (1.0 - strength) * out + strength * smoothed, np.nan).astype(np.float32)
        if preserve_mean:
            mean_now = float(np.nanmean(out[valid]))
            if np.isfinite(mean_now) and abs(mean_now) > 1.0e-30:
                out[valid] *= target_mean / mean_now
    return out


def seamless_deblock_field(
    fine: np.ndarray,
    weights: np.ndarray,
    grid: GeoGrid,
    baseline_sigma_m: float = 1200.0,
    anomaly_sigma_m: float = 1000.0,
    strength: float = 0.95,
    anomaly_min: float = 0.35,
    anomaly_max: float = 2.75,
    preserve_mean: bool = True,
) -> np.ndarray:
    """
    Strong anti-blocking step for dynamically downscaled satellite fields.

    The old deblocking blurred the final field only. That keeps many coarse-pixel
    seams because the field still contains a hard, piecewise-constant satellite
    component. This function separates the field into:

      1. a smooth large-scale baseline, obtained with a NaN-aware Gaussian filter;
      2. a high-resolution multiplicative anomaly from the dynamic weights.

    The recomposed field = smooth_baseline * weight_anomaly. This removes most
    coarse satellite pixel edges while retaining CALMET/DTM/station-driven local
    structure. It preserves the domain mean by default, but does not enforce
    hard conservation inside every original satellite pixel, because that hard
    constraint is precisely what makes the coarse cells visible.
    """
    if strength <= 0 or baseline_sigma_m <= 0:
        return fine.astype(np.float32, copy=True)

    f = fine.astype(np.float64, copy=True)
    w = weights.astype(np.float64, copy=True)
    valid = np.isfinite(f) & np.isfinite(w) & (w > 0)
    if not np.any(valid):
        return fine.astype(np.float32, copy=True)

    baseline_sigma_cells = max(0.01, float(baseline_sigma_m) / float(max(grid.dx, grid.dy)))
    anomaly_sigma_cells = max(0.01, float(anomaly_sigma_m) / float(max(grid.dx, grid.dy)))

    baseline = nan_gaussian_filter(f, baseline_sigma_cells).astype(np.float64)
    smooth_w = nan_gaussian_filter(np.where(valid, w, np.nan), anomaly_sigma_cells).astype(np.float64)

    anomaly = np.ones_like(f, dtype=np.float64)
    ok = valid & np.isfinite(smooth_w) & (smooth_w > 1.0e-30)
    anomaly[ok] = w[ok] / smooth_w[ok]
    anomaly = np.clip(anomaly, float(anomaly_min), float(anomaly_max))

    recomposed = baseline * anomaly
    target_mean = float(np.nanmean(f[valid]))
    if preserve_mean:
        mean_now = float(np.nanmean(recomposed[valid]))
        if np.isfinite(mean_now) and abs(mean_now) > 1.0e-30:
            recomposed[valid] *= target_mean / mean_now

    strength = min(1.0, max(0.0, float(strength)))
    out = np.where(valid, (1.0 - strength) * f + strength * recomposed, np.nan)
    if preserve_mean:
        mean_now = float(np.nanmean(out[valid]))
        if np.isfinite(mean_now) and abs(mean_now) > 1.0e-30:
            out[valid] *= target_mean / mean_now
    return out.astype(np.float32)


def write_pollutant_raster(path: Path, grid: GeoGrid, arr: np.ndarray, tags: Optional[dict] = None, pollutant: str = "POLLUTANT", source_band: int = 1) -> None:
    profile = {
        "driver": "GTiff",
        "height": grid.ny,
        "width": grid.nx,
        "count": 1,
        "dtype": "float32",
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": np.nan,
        "compress": "deflate",
        "tiled": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr.astype(np.float32), 1)
        dst.set_band_description(1, f"{pollutant} dynamically downscaled, conservative")
        base_tags = {
            "method": "conservative_dynamic_downscaling",
            "source_band": str(source_band),
            "pollutant": pollutant,
            "note": "Fine-grid field is model-assisted allocation, not native Sentinel-5P 200 m observation.",
        }
        if tags:
            base_tags.update({k: str(v) for k, v in tags.items()})
        dst.update_tags(**base_tags)

def conservative_downscale(
    s5p_path: Path,
    grid: GeoGrid,
    weights: np.ndarray,
    output_path: Path,
    validate: bool = False,
    band: int = 1,
    pollutant: str = "POLLUTANT",
) -> Optional[dict]:
    out, ref, input_band_array = conservative_downscale_array(s5p_path, grid, weights, band=band)
    write_pollutant_raster(output_path, grid, out, pollutant=pollutant, source_band=band)
    if validate:
        return validate_conservation(ref, input_band_array, out, grid, weights)
    return None

def validate_conservation(src: RasterReference, input_band_array: np.ndarray, fine: np.ndarray, grid: GeoGrid, weights: np.ndarray) -> dict:
    # Recompute overlap-area mean of fine output inside each source pixel and compare to original.
    transformer = Transformer.from_crs(src.crs, grid.crs, always_xy=True).transform
    fine_transform = grid.transform
    inv_transform = ~fine_transform
    diffs = []
    for row in range(src.height):
        for col in range(src.width):
            value = input_band_array[row, col]
            if not np.isfinite(value):
                continue
            if src.nodata is not None and value == src.nodata:
                continue
            x0, y0 = src.transform * (col, row)
            x1, y1 = src.transform * (col + 1, row + 1)
            poly = shapely_transform(transformer, box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
            minx, miny, maxx, maxy = poly.bounds
            c0, r0 = inv_transform * (minx, maxy)
            c1, r1 = inv_transform * (maxx, miny)
            rmin = max(0, int(math.floor(min(r0, r1))) - 1)
            rmax = min(grid.ny - 1, int(math.ceil(max(r0, r1))) + 1)
            cmin = max(0, int(math.floor(min(c0, c1))) - 1)
            cmax = min(grid.nx - 1, int(math.ceil(max(c0, c1))) + 1)
            s = 0.0
            a = 0.0
            for rr in range(rmin, rmax + 1):
                for cc in range(cmin, cmax + 1):
                    if not np.isfinite(fine[rr, cc]):
                        continue
                    inter = poly.intersection(cell_polygon(fine_transform, rr, cc))
                    if inter.is_empty:
                        continue
                    area = inter.area
                    s += float(fine[rr, cc]) * area
                    a += area
            if a > 0:
                diffs.append(s / a - value)
    diffs = np.array(diffs, dtype=float)
    if diffs.size == 0:
        return {"n": 0}
    return {
        "n": int(diffs.size),
        "bias": float(np.nanmean(diffs)),
        "mae": float(np.nanmean(np.abs(diffs))),
        "rmse": float(np.sqrt(np.nanmean(diffs ** 2))),
    }


def write_weight_raster(path: Path, grid: GeoGrid, weights: np.ndarray) -> None:
    profile = {
        "driver": "GTiff",
        "height": grid.ny,
        "width": grid.nx,
        "count": 1,
        "dtype": "float32",
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": np.nan,
        "compress": "deflate",
        "tiled": True,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(weights.astype(np.float32), 1)
        dst.set_band_description(1, "dynamic downscaling weight")


WeightBuilder = Callable[[GeoGrid, Dict[str, np.ndarray]], np.ndarray]
RasterTagBuilder = Callable[[dict], dict]


def identity_raster_tags(tags: dict) -> dict:
    return tags


def deterministic_raster_tags(tags: dict) -> dict:
    out = dict(tags)
    out.update({"method": "deterministic_conservative_dynamic_downscaling"})
    return out


def main(
    weight_builder: WeightBuilder = build_weights,
    raster_tag_builder: RasterTagBuilder = deterministic_raster_tags,
    method_name: str = "deterministic",
    argv: Optional[List[str]] = None,
    include_method_help: bool = False,
) -> None:
    parser = argparse.ArgumentParser(
        description="Conservatively downscale a selected pollutant raster band to a CALMET GEO.DAT grid, optionally corrected with air-quality stations."
    )
    parser.add_argument("input_tif", nargs="?", type=Path, help="Input pollutant GeoTIFF. The selected 1-based band is downscaled.")
    parser.add_argument("calmet_dat", nargs="?", type=Path, help="CALMET/CMET.DAT binary, or NPZ if --met-npz is not used.")
    parser.add_argument("geodat", nargs="?", type=Path, help="CALMET GEO.DAT file.")
    parser.add_argument("output_tif", nargs="?", type=Path, help="Output single-band GeoTIFF.")
    if include_method_help:
        parser.add_argument("--method", choices=["deterministic", "ai"], default=method_name, help="Downscaling weight strategy to use.")
    parser.add_argument("--geodat-sidecar", type=Path, default=None, help="Optional grid JSON fallback.")
    parser.add_argument("--met-npz", type=Path, default=None, help="Optional NPZ with pblh/ws10/u10/v10/ustar on GEO.DAT grid.")
    parser.add_argument("--pollutant", default="NO2", help="Pollutant name used for metadata and the default ground-truth value column, e.g. NO2, O3, PM10, PM25, SO2, CO.")
    parser.add_argument("--input-band", type=int, default=1, help="1-based band number in the input pollutant raster to downscale.")
    parser.add_argument("--groundtruth-value-column", default=None, help="Column name in --groundtruth-csv containing station measurements. Defaults to --pollutant, with NO2 fallback for legacy files.")
    parser.add_argument("--groundtruth-csv", type=Path, default=None, help="Optional CSV with ID,LAT,LON,POLLUTANT station measurements.")
    parser.add_argument("--background-mode", choices=["low-percentile", "mean", "median", "min", "none"], default="low-percentile", help="How to estimate average background POLLUTANT from stations.")
    parser.add_argument("--background-percentile", type=float, default=40.0, help="Percentile threshold used by --background-mode low-percentile.")
    parser.add_argument("--station-alpha", type=float, default=0.65, help="Blend strength for station correction applied to dynamic weights. 0 disables; 1 applies full correction.")
    parser.add_argument("--station-idw-radius", type=float, default=6000.0, help="IDW radius in meters for station correction. Use 0 for global influence.")
    parser.add_argument("--station-idw-power", type=float, default=2.0, help="IDW distance power for station correction.")
    parser.add_argument("--station-ratio-min", type=float, default=0.25, help="Minimum station multiplicative correction ratio.")
    parser.add_argument("--station-ratio-max", type=float, default=4.0, help="Maximum station multiplicative correction ratio.")
    parser.add_argument("--station-direct-ratio", action="store_true", help="Use observed/predicted ratios instead of background-excess ratios.")
    parser.add_argument("--station-report", type=Path, default=None, help="Optional JSON report for station correction/validation.")
    parser.add_argument("--write-correction", type=Path, default=None, help="Optional GeoTIFF of station multiplicative correction field.")
    parser.add_argument("--calmet-selector", choices=["first", "last", "mean"], default="last")
    parser.add_argument("--calmet-stamp", type=int, default=None, help="Select nearest CALMET integer timestamp.")
    parser.add_argument("--allow-no-met", action="store_true", help="Continue with terrain/land-use weights if meteorology cannot be read.")
    parser.add_argument("--validate", action="store_true", help="Print coarse-scale conservation validation statistics.")
    parser.add_argument("--write-weight", type=Path, default=None, help="Optional output GeoTIFF for the final dynamic weight field.")
    parser.add_argument("--deblock-sigma-m", type=float, default=400.0, help="Anti-blocking Gaussian smoothing sigma in meters applied to the final fine field. Use 0 to disable.")
    parser.add_argument("--deblock-strength", type=float, default=0.75, help="Blend strength for anti-blocking smoothing, 0..1.")
    parser.add_argument("--deblock-iterations", type=int, default=1, help="Number of anti-blocking smoothing iterations.")
    parser.add_argument("--no-deblock-mean-preserve", action="store_true", help="Do not restore the domain mean after anti-blocking smoothing.")
    parser.add_argument("--seamless", action=argparse.BooleanOptionalAction, default=True, help="Use strong seam removal by recomposing a smooth large-scale baseline with high-resolution dynamic-weight anomalies. Enabled by default.")
    parser.add_argument("--seamless-baseline-sigma-m", type=float, default=1400.0, help="Gaussian sigma in meters for the smooth large-scale baseline. Larger values remove coarse satellite blocks more aggressively.")
    parser.add_argument("--seamless-anomaly-sigma-m", type=float, default=1000.0, help="Gaussian sigma in meters used to normalize the dynamic-weight anomaly.")
    parser.add_argument("--seamless-strength", type=float, default=0.95, help="Blend strength for seamless recomposition, 0..1.")
    parser.add_argument("--seamless-anomaly-min", type=float, default=0.35, help="Minimum multiplicative dynamic anomaly retained during seamless recomposition.")
    parser.add_argument("--seamless-anomaly-max", type=float, default=2.75, help="Maximum multiplicative dynamic anomaly retained during seamless recomposition.")
    parser.add_argument("--inspect-geodat", type=Path, default=None, help="Inspect/infer a GEO.DAT and exit.")
    parser.add_argument("--inspect-calmet", type=Path, default=None, help="List likely gridded CALMET records and exit.")
    parser.add_argument("--inspect-groundtruth", type=Path, default=None, help="Inspect a ground-truth CSV and exit.")
    args = parser.parse_args(argv)

    if args.inspect_geodat:
        grid = GeoDATReader(args.inspect_geodat, args.geodat_sidecar).read()
        print(json.dumps(grid.as_dict(), indent=2))
        if grid.elevation is not None:
            print("elevation:", json.dumps({
                "min": float(np.nanmin(grid.elevation)),
                "max": float(np.nanmax(grid.elevation)),
                "mean": float(np.nanmean(grid.elevation)),
            }, indent=2))
        if grid.landuse is not None:
            vals, cnt = np.unique(grid.landuse, return_counts=True)
            print("landuse:", json.dumps({str(int(v)): int(c) for v, c in zip(vals, cnt)}, indent=2))
        return

    if args.inspect_calmet:
        print(json.dumps(MetReader.inspect(args.inspect_calmet), indent=2))
        return

    if args.inspect_groundtruth:
        st = read_groundtruth_csv(args.inspect_groundtruth, args.groundtruth_value_column or args.pollutant)
        bg = estimate_background(st.value, args.background_mode, args.background_percentile)
        print(json.dumps({"pollutant": args.pollutant, "stations": st.as_summary(), "background_value": bg}, indent=2))
        return

    missing = [name for name in ["input_tif", "calmet_dat", "geodat", "output_tif"] if getattr(args, name) is None]
    if missing:
        parser.error("missing required positional arguments: " + ", ".join(missing))

    grid = GeoDATReader(args.geodat, args.geodat_sidecar).read()
    met = MetReader(
        args.calmet_dat,
        grid,
        met_npz=args.met_npz,
        selector=args.calmet_selector,
        stamp=args.calmet_stamp,
        allow_no_met=args.allow_no_met,
    ).read()
    print("Inferred target grid:", json.dumps(grid.as_dict(), indent=2))
    print("Meteorology fields:", ", ".join(sorted(met.keys())) if met else "none")

    weights = weight_builder(grid, met)
    report = {
        "grid": grid.as_dict(),
        "meteorology_fields": sorted(met.keys()),
        "method": method_name,
        "pollutant": args.pollutant,
        "input_band": args.input_band,
        "groundtruth_used": False,
        "deblocking": {
            "sigma_m": args.deblock_sigma_m,
            "strength": args.deblock_strength,
            "iterations": args.deblock_iterations,
            "mean_preserve": not args.no_deblock_mean_preserve,
            "note": "Anti-blocking is a soft regularization step; strict per-satellite-pixel conservation can reveal coarse pixel boundaries."
        },
        "seamless": {
            "enabled": args.seamless,
            "baseline_sigma_m": args.seamless_baseline_sigma_m,
            "anomaly_sigma_m": args.seamless_anomaly_sigma_m,
            "strength": args.seamless_strength,
            "anomaly_min": args.seamless_anomaly_min,
            "anomaly_max": args.seamless_anomaly_max,
            "note": "Seamless recomposition removes the hard piecewise-constant satellite component by using a smooth baseline multiplied by high-resolution dynamic anomalies."
        },
    }
    def apply_deblock(arr: np.ndarray, weight_field: np.ndarray) -> np.ndarray:
        out = arr
        if args.seamless:
            out = seamless_deblock_field(
                out,
                weight_field,
                grid,
                baseline_sigma_m=args.seamless_baseline_sigma_m,
                anomaly_sigma_m=args.seamless_anomaly_sigma_m,
                strength=args.seamless_strength,
                anomaly_min=args.seamless_anomaly_min,
                anomaly_max=args.seamless_anomaly_max,
                preserve_mean=not args.no_deblock_mean_preserve,
            )
        out = deblock_fine_field(
            out,
            grid,
            sigma_m=args.deblock_sigma_m,
            strength=args.deblock_strength,
            iterations=args.deblock_iterations,
            preserve_mean=not args.no_deblock_mean_preserve,
        )
        return out


    if args.groundtruth_csv:
        stations = read_groundtruth_csv(args.groundtruth_csv, args.groundtruth_value_column or args.pollutant).with_xy(grid.crs)
        background = estimate_background(stations.value, args.background_mode, args.background_percentile)
        print("Ground-truth stations:", json.dumps(stations.as_summary(), indent=2))
        print(f"Estimated average background {args.pollutant} ({args.background_mode}): {background:.12g}")

        base_field, _, _ = conservative_downscale_array(args.input_tif, grid, weights, band=args.input_band)
        pred_before, _, _ = sample_grid_values(base_field, grid, stations.x, stations.y)
        before = station_metrics(stations.value, pred_before)
        correction, corr_info = build_station_correction(
            grid=grid,
            base_field=base_field,
            stations=stations,
            background_value=background,
            alpha=args.station_alpha,
            power=args.station_idw_power,
            radius_m=args.station_idw_radius,
            ratio_min=args.station_ratio_min,
            ratio_max=args.station_ratio_max,
            use_background_excess=not args.station_direct_ratio,
        )
        if args.write_correction:
            write_weight_raster(args.write_correction, grid, correction)
            print(f"Wrote station correction raster: {args.write_correction}")
        weights = weights * correction
        final_conservative_field, final_ref, final_input_band_array = conservative_downscale_array(args.input_tif, grid, weights, band=args.input_band)
        pred_after_conservative, _, _ = sample_grid_values(final_conservative_field, grid, stations.x, stations.y)
        after_conservative = station_metrics(stations.value, pred_after_conservative)
        final_field = apply_deblock(final_conservative_field, weights)
        pred_after_regularized, _, _ = sample_grid_values(final_field, grid, stations.x, stations.y)
        after_regularized = station_metrics(stations.value, pred_after_regularized)
        write_pollutant_raster(
            args.output_tif,
            grid,
            final_field,
                tags=raster_tag_builder({
                "groundtruth_csv": str(args.groundtruth_csv),
                "groundtruth_value_column": args.groundtruth_value_column or args.pollutant,
                "pollutant": args.pollutant,
                "input_band": args.input_band,
                "station_background_value": background,
                "station_alpha": args.station_alpha,
                "deblock_sigma_m": args.deblock_sigma_m,
                "deblock_strength": args.deblock_strength,
                "deblock_iterations": args.deblock_iterations,
                "seamless": args.seamless,
                "seamless_baseline_sigma_m": args.seamless_baseline_sigma_m,
                "seamless_anomaly_sigma_m": args.seamless_anomaly_sigma_m,
                "seamless_strength": args.seamless_strength,
            }),
            pollutant=args.pollutant,
            source_band=args.input_band,
        )
        stats = None
        if args.validate:
            stats = {
                "conservative_allocation": validate_conservation(final_ref, final_input_band_array, final_conservative_field, grid, weights),
                "written_regularized_output": validate_conservation(final_ref, final_input_band_array, final_field, grid, weights),
            }

        report.update({
            "groundtruth_used": True,
            "stations": stations.as_summary(),
            "background_mode": args.background_mode,
            "background_percentile": args.background_percentile,
            "background_value": background,
            "station_metrics_before_correction": before,
            "station_metrics_after_correction_conservative": after_conservative,
            "station_metrics_after_correction_regularized": after_regularized,
            "station_correction": corr_info,
        })
        print("Station metrics before correction:", json.dumps(before, indent=2))
        print("Station metrics after correction before regularization:", json.dumps(after_conservative, indent=2))
        print("Station metrics after correction in written regularized output:", json.dumps(after_regularized, indent=2))
    else:
        if args.write_weight:
            write_weight_raster(args.write_weight, grid, weights)
            print(f"Wrote weight raster: {args.write_weight}")
        conservative_out, ref, input_band_array = conservative_downscale_array(args.input_tif, grid, weights, band=args.input_band)
        out = apply_deblock(conservative_out, weights)
        write_pollutant_raster(
            args.output_tif,
            grid,
            out,
            tags=raster_tag_builder({
                "deblock_sigma_m": args.deblock_sigma_m,
                "deblock_strength": args.deblock_strength,
                "deblock_iterations": args.deblock_iterations,
                "seamless": args.seamless,
                "seamless_baseline_sigma_m": args.seamless_baseline_sigma_m,
                "seamless_anomaly_sigma_m": args.seamless_anomaly_sigma_m,
                "seamless_strength": args.seamless_strength,
            }),
            pollutant=args.pollutant,
            source_band=args.input_band,
        )
        stats = None
        if args.validate:
            stats = {
                "conservative_allocation": validate_conservation(ref, input_band_array, conservative_out, grid, weights),
                "written_regularized_output": validate_conservation(ref, input_band_array, out, grid, weights),
            }

    if args.groundtruth_csv and args.write_weight:
        write_weight_raster(args.write_weight, grid, weights)
        print(f"Wrote final weight raster: {args.write_weight}")

    print(f"Wrote output: {args.output_tif}")
    if stats is not None:
        print("Conservation validation:", json.dumps(stats, indent=2))
        report["conservation_validation"] = stats
    if args.station_report:
        args.station_report.parent.mkdir(parents=True, exist_ok=True)
        args.station_report.write_text(json.dumps(report, indent=2))
        print(f"Wrote station report: {args.station_report}")


if __name__ == "__main__":
    main()
