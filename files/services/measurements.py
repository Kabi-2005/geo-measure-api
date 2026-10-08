"""CRS selection and area / length calculation.

Strategy: every feature is reprojected to the UTM zone that contains its
centroid (WGS84 / UTM, metres) before measuring. Polar features (|lat| > 80)
use UPS. Lat/lon degrees are never used for distance or area.
"""
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

WGS84 = CRS.from_epsg(4326)
POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "MultiLineString", "LinearRing"}
POINTLIKE = {"Point", "MultiPoint"}


@dataclass
class Measurement:
    status: str
    area_m2: Optional[float] = None
    length_m: Optional[float] = None
    projected_crs: str = ""
    note: str = ""


@lru_cache(maxsize=256)
def _transformer(src_wkt: str, dst_epsg: int) -> Transformer:
    return Transformer.from_crs(CRS.from_wkt(src_wkt), CRS.from_epsg(dst_epsg), always_xy=True)


def utm_epsg_for(lon: float, lat: float) -> int:
    """EPSG code of the UTM (or UPS at the poles) zone containing lon/lat."""
    if lat > 80:
        return 32661   # WGS84 / UPS North
    if lat < -80:
        return 32761   # WGS84 / UPS South
    zone = int((lon + 180) // 6) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


def _centroid_lonlat(geom: BaseGeometry, src: CRS):
    """Centroid of the feature expressed in lon/lat (used only to pick a zone)."""
    c = geom.representative_point() if geom.geom_type in POLYGONAL else geom.centroid
    if src.is_geographic:
        return c.x, c.y
    to_wgs = Transformer.from_crs(src, WGS84, always_xy=True)
    return to_wgs.transform(c.x, c.y)


def measure(geom: Optional[BaseGeometry], src: CRS) -> Measurement:
    if geom is None or geom.is_empty:
        return Measurement("EMPTY", note="Geometry is null or empty.")

    gtype = geom.geom_type
    if gtype in POINTLIKE:
        return Measurement("NOT_REQUIRED", note="No measurement defined for points.")
    if gtype not in POLYGONAL | LINEAR:
        return Measurement("UNSUPPORTED", note=f"Measurement not supported for {gtype}.")

    try:
        lon, lat = _centroid_lonlat(geom, src)
        epsg = utm_epsg_for(lon, lat)
        tf = _transformer(src.to_wkt(), epsg)
        projected = transform(tf.transform, geom)
        # Drop Z so 3D KML coordinates do not matter
        projected = transform(lambda x, y, z=None: (x, y), projected)
    except Exception as exc:  # defensive: bad coordinates, projection failure
        return Measurement("ERROR", note=f"Reprojection failed: {exc}")

    crs_label = f"EPSG:{epsg}"
    if gtype in POLYGONAL:
        return Measurement("OK", area_m2=projected.area, projected_crs=crs_label)
    return Measurement("OK", length_m=projected.length, projected_crs=crs_label)
