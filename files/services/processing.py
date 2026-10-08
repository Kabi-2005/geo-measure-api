import logging
import shutil
import tempfile
from pathlib import Path

from django.db import transaction
from shapely.geometry import mapping

from ..models import Feature, UploadedFile
from . import measurements, readers
from .exceptions import FileProcessingError

log = logging.getLogger(__name__)
BATCH = 500


def process_file(obj: UploadedFile) -> UploadedFile:
    """Read the stored upload, persist features + measurements, update status."""
    workdir = Path(tempfile.mkdtemp(prefix="geo_"))
    try:
        result = readers.read_upload(Path(obj.file.path), workdir)
        crs_label = _crs_label(result.crs)

        rows = []
        for raw in result.features:
            m = measurements.measure(raw.geometry, result.crs)
            rows.append(
                Feature(
                    file=obj,
                    index=raw.index,
                    layer=raw.layer,
                    geometry_type=raw.geometry.geom_type if raw.geometry else "None",
                    geometry=mapping(raw.geometry) if raw.geometry else None,
                    crs=crs_label,
                    properties=raw.properties,
                    area_m2=m.area_m2,
                    length_m=m.length_m,
                    projected_crs=m.projected_crs,
                    measurement_status=m.status,
                    measurement_note=m.note,
                )
            )

        with transaction.atomic():
            Feature.objects.bulk_create(rows, batch_size=BATCH)
            obj.crs = crs_label
            obj.feature_count = len(rows)
            obj.status = UploadedFile.Status.COMPLETED
            obj.error = ""
            obj.save()
    except FileProcessingError as exc:
        _fail(obj, str(exc))
    except Exception:  # unexpected: log full trace, expose a generic message
        log.exception("Unexpected failure processing file %s", obj.pk)
        _fail(obj, "Unexpected error while processing the file.")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return obj


def _fail(obj: UploadedFile, message: str) -> None:
    obj.status = UploadedFile.Status.FAILED
    obj.error = message
    obj.save()


def _crs_label(crs) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else crs.name
