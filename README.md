# Geospatial File Measurement API

A Django + Django REST Framework service that accepts a Shapefile (`.zip`) or `.kml`,
extracts every feature, and returns **area** (polygons) and **length** (lines) calculated
in a projected CRS.

## Setup

```bash
python -m venv venv
venv\Scripts\activate            # Windows (cmd). macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver        # http://localhost:8000
python manage.py test             # run the test suite
```

Requires Python 3.10+. `geopandas`/`pyogrio` ship wheels with GDAL bundled, so no system GDAL install is needed.

## Configuration

All settings come from environment variables (see `.env.example`). Defaults are production-safe:
`DEBUG` is off, only localhost hosts are allowed, and a random secret key is generated per process.
The app runs out of the box with no configuration.

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_DEBUG` | `0` | Set `1` for local development only |
| `DJANGO_SECRET_KEY` | random per process | Set a fixed value in real deployments |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | Comma-separated hostnames |
| `DJANGO_SECURE` | `0` | `1` behind HTTPS: SSL redirect, HSTS, secure cookies |
| `DJANGO_LOG_LEVEL` | `INFO` | Logging level |
| `MAX_UPLOAD_MB` / `MAX_UNCOMPRESSED_MB` | `50` / `500` | Upload and zip-bomb limits |
| `DJANGO_DB_PATH` / `DJANGO_MEDIA_ROOT` | project folder | Where data and uploads live |

Windows (cmd): `set DJANGO_DEBUG=1`. macOS/Linux: `export DJANGO_DEBUG=1`.

## Try it

Two sample files are in `samples/`. With the server running:

```bash
curl -F "file=@samples/survey.kml" http://localhost:8000/api/files/
curl -F "file=@samples/plot_shapefile.zip" http://localhost:8000/api/files/
```

Then open `http://localhost:8000/api/files/<id>/measurements/` using the `id` returned.
`make_sample_shapefile.py` regenerates the Shapefile sample. Both samples contain the same
0.01 x 0.01 degree square near 13N 77E, so both report an area of about 1,200,593 m2.

## API

### `POST /api/files/`  - upload and process
`multipart/form-data` with a `file` field (`.zip` containing a Shapefile, or `.kml`).

```bash
curl -F "file=@samples/survey.kml" http://localhost:8000/api/files/
```
```json
{
  "id": "2db950d0-cbaf-47a1-9115-4e5ab0112a44",
  "filename": "survey.kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "error": "",
  "created_at": "2026-10-08T04:38:53Z"
}
```
| Code | Meaning |
|---|---|
| 201 | Processed successfully |
| 400 | Invalid upload (wrong extension, too large, no file) |
| 422 | File accepted but could not be processed (`status: FAILED`, reason in `error`) |

**Why 422 for a failed file?** The request itself is well-formed (so not 400) and the server is healthy (so not 500),
but the *content* cannot be processed (corrupt zip, missing `.prj`, mixed CRS). 422 Unprocessable Content expresses exactly that.
The failed record is still stored, so `GET /api/files/{id}/` returns `status: FAILED` with the reason, and
`GET .../measurements/` returns `409` because there is nothing to measure.

### `GET /api/files/{id}/`  - file information
Same shape as the response above. `404` for an unknown id.

### `GET /api/files/{id}/measurements/`  - per-feature measurements
Query params: `?page=2`, `?geometry_type=Polygon`. Returns `409` if the file is not `COMPLETED`.

```json
{
  "file_id": "2db950d0-cbaf-47a1-9115-4e5ab0112a44",
  "filename": "survey.kml",
  "crs": "EPSG:4326",
  "summary": {"total_area_m2": 1200592.74, "total_length_m": 5425.31, "count": 3},
  "count": 3, "next": null, "previous": null,
  "features": [
    {
      "id": 0,
      "layer": "survey",
      "geometry_type": "Polygon",
      "crs": "EPSG:4326",
      "properties": {"Name": "plot", "description": null, "timestamp": null},
      "geometry": {"type": "Polygon", "coordinates": [[[77.0, 13.0], "..."]]},
      "measurements": {
        "status": "OK",
        "area_m2": 1200592.74,
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
| Synchronous processing | Simplest correct design for the scope; the API response already contains the final status. Upload size is capped to bound request time. | Celery/RQ job queue (see future scope) |
| Environment-based settings with safe defaults | Same code runs locally and in production; nothing secret is committed | Hardcoded settings, `python-dotenv`, per-environment settings modules |
| `status` field on the file anyway | The API contract already supports async without changes | n/a |
| Store GeoJSON in a `JSONField` | Works on SQLite with zero setup | PostGIS `GeometryField` for spatial queries and indexing |
| Measurements computed at upload and stored | Reads are fast and results are reproducible | Compute on request |
| GeoPandas + pyogrio | One reader for Shapefile and KML, bundled GDAL | Fiona (slower, older), hand-parsing KML with `lxml` |
| Per-feature failure isolation | One bad geometry should not reject a 10,000-feature file | Fail the whole file |
| Rejecting missing CRS / mixed-CRS zips | Correctness over convenience | Assume EPSG:4326 |

Accuracy is verified in tests by comparing results with `pyproj.Geod` (ellipsoidal geodesic), agreeing within 0.1%.

## Learning

- **CRS is the core of the problem.** Measuring on latitude/longitude degrees gives meaningless numbers, so every feature is reprojected to a metre-based UTM zone first. I checked the results against `pyproj.Geod` to prove the projection choice was accurate.
- **Shapefiles are several files.** The `.prj` carries the CRS, so a zip without it is rejected rather than guessed.
- **Real data is messy.** While testing a KML upload, pandas `NaT` (not-a-time) values appeared in the JSON as the string `"NaT"`. I fixed the JSON conversion and added a test so it stays fixed.
- **Stored filenames leak into data.** GDAL names a KML layer after the file on disk, so a renamed duplicate upload (`survey_hCz9JRG.kml`) changed the layer name. Storing each upload in its own folder (`uploads/<id>/`) keeps the original name.
- **Untrusted zips need defences** against zip-slip and decompression bombs.
- **Failure isolation matters.** One unsupported or empty geometry should be reported on that feature, not fail the whole file.
- **Debugging environment issues:** a 404 from another Django project already on the same port taught me to check which URLconf is answering before suspecting my own code.

## Future Scope

- Async processing with Celery + Redis; add `PROCESSING` polling and webhooks for large files.
- PostGIS backend with spatial indexes (bbox filter, intersects, within).
- Geodesic measurement (`pyproj.Geod`) as an option or cross-check, especially for features spanning several UTM zones or the antimeridian.
- More formats: GeoJSON, GeoPackage, GPX; reprojection of output geometry via `?crs=` parameter.
- Authentication, per-user file ownership, rate limiting, and object storage (S3) for uploads.
- Optional geometry simplification and a `?include_geometry=false` flag to shrink large responses.
- Geometry validity checks and repair (`make_valid`) with warnings in the response.
- OpenAPI docs (drf-spectacular), Dockerfile, CI (GitHub Actions), and a periodic cleanup job for old uploads.
