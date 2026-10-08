# Geospatial File Measurement API

A Django + Django REST Framework service that accepts a Shapefile (`.zip`) or `.kml`,
extracts every feature, and returns **area** (polygons) and **length** (lines) calculated
in a projected CRS.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
python manage.py test            # run the test suite
```

Requires Python 3.10+. `geopandas`/`pyogrio` ship wheels with GDAL bundled, so no system GDAL install is needed.

## API

### `POST /api/files/`  - upload and process
`multipart/form-data` with a `file` field (`.zip` containing a Shapefile, or `.kml`).

```bash
curl -F "file=@survey.kml" http://localhost:8000/api/files/
```
```json
{
  "id": "6f1c0b0e-5b0e-4a77-9a0e-0c6f0d0c2a11",
  "filename": "survey.kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error": "",
  "created_at": "2026-10-08T10:00:00Z"
}
```
| Code | Meaning |
|---|---|
| 201 | Processed successfully |
| 400 | Invalid upload (wrong extension, too large, no file) |
| 422 | File accepted but could not be processed (`status: FAILED`, reason in `error`) |

### `GET /api/files/{id}/`  - file information
Same shape as the response above. `404` for an unknown id.

### `GET /api/files/{id}/measurements/`  - per-feature measurements
Query params: `?page=2`, `?geometry_type=Polygon`. Returns `409` if the file is not `COMPLETED`.

```json
{
  "file_id": "6f1c0b0e-...",
  "filename": "survey.kml",
  "crs": "EPSG:4326",
  "summary": {"total_area_m2": 1198113.4, "total_length_m": 5412.7, "count": 3},
  "count": 3, "next": null, "previous": null,
  "features": [
    {
      "id": 0,
      "layer": "Document",
      "geometry_type": "Polygon",
      "crs": "EPSG:4326",
      "properties": {"Name": "plot", "description": null},
      "geometry": {"type": "Polygon", "coordinates": [[[77.0, 13.0], "..."]]},
      "measurements": {
        "status": "OK",
        "area_m2": 1198113.4,
        "length_m": null,
        "projected_crs": "EPSG:32643",
        "note": null
      }
    },
    {"id": 2, "geometry_type": "Point",
     "measurements": {"status": "NOT_REQUIRED", "area_m2": null, "length_m": null,
                      "projected_crs": null, "note": "No measurement defined for points."}}
  ]
}
```
Measurement `status` values: `OK`, `NOT_REQUIRED` (points), `UNSUPPORTED` (e.g. GeometryCollection), `EMPTY` (null geometry), `ERROR` (reprojection failed). A bad feature never fails the whole file.

## Architecture

```
config/               Django project settings and root URLs
files/
  models.py           UploadedFile (job/status) and Feature (geometry + measurements)
  serializers.py      Upload validation, response shaping
  views.py            Thin API views
  services/
    readers.py        Shapefile-zip and KML parsing -> plain RawFeature objects
    measurements.py   CRS selection + area/length calculation (pure functions)
    processing.py     Orchestrates read -> measure -> persist -> status
  tests/              Unit + API tests
```

**File-processing flow**
1. View validates extension and size, stores the upload, creates `UploadedFile(PROCESSING)`.
2. `readers.read_upload` extracts the zip safely (zip-slip and zip-bomb guards), finds `.shp` files, or lists KML layers, and reads them with GeoPandas/pyogrio.
3. Each row becomes a `RawFeature` (index, layer, shapely geometry, JSON-safe properties).
4. `processing.process_file` measures every feature and bulk-inserts `Feature` rows in one transaction.
5. File is marked `COMPLETED`, or `FAILED` with a user-readable `error`.

**Measurement flow**: for each feature, `measurements.measure` classifies the geometry type, picks a projected CRS, reprojects, and reads `.area` or `.length` in metres. Results are stored so the measurements endpoint is a cheap read.

**CRS handling**
- KML is always WGS84 (EPSG:4326) by specification.
- Shapefile CRS comes from the `.prj`. A missing `.prj` is rejected, because guessing a CRS would silently produce wrong numbers.
- Degrees are never used for area or length. Each feature is reprojected to the **UTM zone of its representative point** (UPS beyond 80 degrees latitude), then measured in metres.
- Geometry and properties are stored in the **source CRS**; only measurements use the projected CRS. The projected CRS used is reported per feature.

## Design Decisions

| Decision | Why | Alternatives considered |
|---|---|---|
| Per-feature UTM zone | Simple, standard, accurate to roughly 0.1% for normal-sized features | Equal-area projection (best for area, worse for length); geodesic calculation on the ellipsoid (most accurate, especially for features spanning many zones). A natural upgrade. |
| Synchronous processing | Simplest correct design for the scope; the API response already contains the final status | Celery/RQ job queue (see future scope) |
| `status` field on the file anyway | The API contract already supports async without changes | n/a |
| Store GeoJSON in a `JSONField` | Works on SQLite with zero setup | PostGIS `GeometryField` for spatial queries and indexing |
| Measurements computed at upload and stored | Reads are fast and results are reproducible | Compute on request |
| GeoPandas + pyogrio | One reader for Shapefile and KML, bundled GDAL | Fiona (slower, older), hand-parsing KML with `lxml` |
| Per-feature failure isolation | One bad geometry should not reject a 10,000-feature file | Fail the whole file |
| Rejecting missing CRS / mixed-CRS zips | Correctness over convenience | Assume EPSG:4326 |

Accuracy is verified in tests by comparing results with `pyproj.Geod` (ellipsoidal geodesic), agreeing within 0.1%.

## Learning

- Shapefiles are really several files (`.shp/.shx/.dbf/.prj`), and the `.prj` is what makes the data trustworthy.
- Computing area or length on lat/lon degrees is wrong. Distortion grows with latitude, so the CRS must be chosen deliberately.
- KML has no per-file CRS, but it has layers (Folders) that GDAL exposes separately.
- Untrusted zip files need explicit defences (zip-slip, decompression bombs).
- Real-world data is messy: NaN/NaT values, null geometries, 3D coordinates, mixed geometry types. All need handling before storing as JSON.

## Future Scope

- Async processing with Celery + Redis; add `PROCESSING` polling and webhooks for large files.
- PostGIS backend with spatial indexes (bbox filter, intersects, within).
- Geodesic measurement (`pyproj.Geod`) as an option or cross-check, especially for features spanning several UTM zones or the antimeridian.
- More formats: GeoJSON, GeoPackage, GPX; reprojection of output geometry via `?crs=` parameter.
- Authentication, per-user file ownership, rate limiting, and object storage (S3) for uploads.
- Optional geometry simplification and a `?include_geometry=false` flag to shrink large responses.
- Geometry validity checks and repair (`make_valid`) with warnings in the response.
- OpenAPI docs (drf-spectacular), Dockerfile, CI (GitHub Actions), and a periodic cleanup job for old uploads.
