import io
import shutil
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from pyproj import Geod
from rest_framework.test import APITestCase
from shapely.geometry import LineString, Point, Polygon

from files.services.measurements import measure, utm_epsg_for
from files.services.readers import _json_safe
import numpy as np
import pandas as pd
from pyproj import CRS

GEOD = Geod(ellps="WGS84")
POLY = Polygon([(77.00, 13.00), (77.01, 13.00), (77.01, 13.01), (77.00, 13.01)])
LINE = LineString([(77.0, 13.0), (77.05, 13.0)])

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>plot</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
77.00,13.00,0 77.01,13.00,0 77.01,13.01,0 77.00,13.01,0 77.00,13.00,0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
<Placemark><name>road</name><LineString><coordinates>77.0,13.0,0 77.05,13.0,0</coordinates></LineString></Placemark>
<Placemark><name>pin</name><Point><coordinates>77.0,13.0,0</coordinates></Point></Placemark>
</Document></kml>"""


def shapefile_zip(gdf) -> bytes:
    tmp = Path(tempfile.mkdtemp())
    try:
        gdf.to_file(tmp / "data.shp")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for p in tmp.iterdir():
                zf.write(p, p.name)
        return buf.getvalue()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class MeasurementUnitTests(APITestCase):
    def test_utm_zone_selection(self):
        self.assertEqual(utm_epsg_for(77.0, 13.0), 32643)    # north
        self.assertEqual(utm_epsg_for(151.2, -33.9), 32756)  # south
        self.assertEqual(utm_epsg_for(0, 85), 32661)         # UPS

    def test_unsupported_geometry_is_graceful(self):
        from shapely.geometry import GeometryCollection
        m = measure(GeometryCollection([POLY, LINE]), CRS.from_epsg(4326))
        self.assertEqual(m.status, "UNSUPPORTED")

    def test_empty_geometry(self):
        self.assertEqual(measure(None, CRS.from_epsg(4326)).status, "EMPTY")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class FileApiTests(APITestCase):
    def _upload(self, name, content):
        return self.client.post(
            "/api/files/", {"file": SimpleUploadedFile(name, content)}, format="multipart"
        )

    def test_kml_end_to_end(self):
        r = self._upload("survey.kml", KML.encode())
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "COMPLETED")
        self.assertEqual(r.data["feature_count"], 3)
        self.assertEqual(r.data["crs"], "EPSG:4326")

        info = self.client.get(f"/api/files/{r.data['id']}/")
        self.assertEqual(info.data["filename"], "survey.kml")

        m = self.client.get(f"/api/files/{r.data['id']}/measurements/").data
        by_type = {f["geometry_type"]: f for f in m["features"]}

        true_area = abs(GEOD.geometry_area_perimeter(POLY)[0])
        got = by_type["Polygon"]["measurements"]["area_m2"]
        self.assertAlmostEqual(got / true_area, 1.0, places=3)

        true_len = GEOD.geometry_length(LINE)
        got = by_type["LineString"]["measurements"]["length_m"]
        self.assertAlmostEqual(got / true_len, 1.0, places=3)

        self.assertEqual(by_type["Point"]["measurements"]["status"], "NOT_REQUIRED")
        self.assertEqual(by_type["Polygon"]["measurements"]["projected_crs"], "EPSG:32643")

    def test_shapefile_zip_in_projected_crs(self):
        gdf = gpd.GeoDataFrame(
            {"name": ["a", "b"]}, geometry=[POLY, LINE], crs="EPSG:4326"
        ).to_crs(32643)
        # shapefiles need homogeneous geometry types: split
        poly_only = gdf.iloc[[0]]
        r = self._upload("p.zip", shapefile_zip(poly_only))
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["crs"], "EPSG:32643")
        m = self.client.get(f"/api/files/{r.data['id']}/measurements/").data
        true_area = abs(GEOD.geometry_area_perimeter(POLY)[0])
        got = m["features"][0]["measurements"]["area_m2"]
        self.assertAlmostEqual(got / true_area, 1.0, places=3)
        self.assertEqual(m["features"][0]["properties"]["name"], "a")

    def test_rejects_bad_extension(self):
        self.assertEqual(self._upload("x.txt", b"hi").status_code, 400)

    def test_corrupt_zip_marked_failed(self):
        r = self._upload("bad.zip", b"not a zip")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.data["status"], "FAILED")
        m = self.client.get(f"/api/files/{r.data['id']}/measurements/")
        self.assertEqual(m.status_code, 409)

    def test_shapefile_without_prj_fails_cleanly(self):
        gdf = gpd.GeoDataFrame(geometry=[POLY], crs="EPSG:4326")
        tmp = Path(tempfile.mkdtemp())
        gdf.to_file(tmp / "d.shp")
        (tmp / "d.prj").unlink()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for p in tmp.iterdir():
                zf.write(p, p.name)
        r = self._upload("noprj.zip", buf.getvalue())
        self.assertEqual(r.status_code, 422)
        self.assertIn("CRS", r.data["error"])

    def test_nat_values_become_null(self):
        """Regression: pandas NaT used to be stored as the string "NaT"."""
        r = self._upload("survey.kml", KML.encode())
        m = self.client.get(f"/api/files/{r.data['id']}/measurements/").data
        for feature in m["features"]:
            for key in ("timestamp", "begin", "end"):
                self.assertIsNone(feature["properties"][key], key)

    def test_failed_file_uses_422_and_reports_reason(self):
        r = self._upload("bad.zip", b"not a zip")
        self.assertEqual(r.status_code, 422)
        self.assertTrue(r.data["error"])
        info = self.client.get(f"/api/files/{r.data['id']}/")
        self.assertEqual(info.data["status"], "FAILED")

    def test_unknown_id_404(self):
        self.assertEqual(
            self.client.get("/api/files/00000000-0000-0000-0000-000000000000/").status_code, 404
        )


class JsonSafeTests(APITestCase):
    def test_scalar_conversion(self):
        self.assertIsNone(_json_safe(pd.NaT))
        self.assertIsNone(_json_safe(float("nan")))
        self.assertIsNone(_json_safe(np.nan))
        self.assertEqual(_json_safe(np.int64(5)), 5)
        self.assertEqual(_json_safe(pd.Timestamp("2026-01-02")), "2026-01-02T00:00:00")
