"""Read Shapefile (.zip) and KML uploads into plain feature dicts."""
import datetime as dt
import math
import zipfile
from dataclasses import dataclass
from pathlib import Path
import pandas as pd

import geopandas as gpd
import pyogrio
from django.conf import settings
from pyproj import CRS

from .exceptions import FileProcessingError

KML_CRS = CRS.from_epsg(4326)  # KML is always WGS84 lon/lat by spec


@dataclass
class RawFeature:
    index: int
    layer: str
    geometry: object          # shapely geometry or None
    properties: dict


@dataclass
class ReadResult:
    crs: CRS
    features: list


def read_upload(path: Path, workdir: Path) -> ReadResult:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        return _read_shapefile_zip(path, workdir)
    if suffix == ".kml":
        return _read_kml(path)
    raise FileProcessingError("Unsupported file type. Upload a .zip (Shapefile) or .kml.")


# --------------------------------------------------------------------- zip
def _safe_extract(zip_path: Path, dest: Path) -> None:
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise FileProcessingError("Uploaded file is not a valid zip archive.") from exc
    with zf:
        total = sum(i.file_size for i in zf.infolist())
        if total > settings.MAX_UNCOMPRESSED_BYTES:
            raise FileProcessingError("Zip archive is too large when uncompressed.")
        dest_resolved = dest.resolve()
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest_resolved)):  # zip-slip guard
                raise FileProcessingError("Zip contains an unsafe path.")
        zf.extractall(dest)


def _read_shapefile_zip(zip_path: Path, workdir: Path) -> ReadResult:
    extract_dir = workdir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    _safe_extract(zip_path, extract_dir)

    shp_files = [
        p for p in extract_dir.rglob("*.shp") if "__MACOSX" not in p.parts
    ]
    if not shp_files:
        raise FileProcessingError("No .shp file found inside the zip archive.")

    features, crs, idx = [], None, 0
    for shp in sorted(shp_files):
        gdf = _read_gdf(shp)
        if gdf.crs is None:
            raise FileProcessingError(
                f"'{shp.name}' has no CRS (missing .prj). Cannot measure safely."
            )
        if crs is None:
            crs = CRS.from_user_input(gdf.crs)
        elif CRS.from_user_input(gdf.crs) != crs:
            raise FileProcessingError(
                "Zip contains shapefiles with different CRSs; upload them separately."
            )
        for geom, props in _iter_rows(gdf):
            features.append(RawFeature(idx, shp.stem, geom, props))
            idx += 1
    return ReadResult(crs=crs, features=features)


# --------------------------------------------------------------------- kml
def _read_kml(path: Path) -> ReadResult:
    try:
        layers = pyogrio.list_layers(path)
    except Exception as exc:
        raise FileProcessingError(f"Could not parse KML: {exc}") from exc

    features, idx = [], 0
    for layer_name, _geom_type in layers:
        gdf = _read_gdf(path, layer=layer_name)
        for geom, props in _iter_rows(gdf):
            features.append(RawFeature(idx, str(layer_name), geom, props))
            idx += 1
    return ReadResult(crs=KML_CRS, features=features)


# ----------------------------------------------------------------- helpers
def _read_gdf(path: Path, **kwargs) -> gpd.GeoDataFrame:
    try:
        return gpd.read_file(path, engine="pyogrio", **kwargs)
    except Exception as exc:
        raise FileProcessingError(f"Could not read '{path.name}': {exc}") from exc


def _iter_rows(gdf: gpd.GeoDataFrame):
    geom_col = gdf.geometry.name
    for _, row in gdf.iterrows():
        geom = row[geom_col]
        props = {k: _json_safe(v) for k, v in row.items() if k != geom_col}
        yield (None if geom is None or geom.is_empty else geom), props


def _json_safe(value):
    """Convert numpy / pandas scalars so they can be stored in a JSONField."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, (list, tuple, dict, set)):
        return str(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
