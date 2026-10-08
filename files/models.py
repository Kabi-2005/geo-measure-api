import uuid

from django.db import models


class UploadedFile(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "PROCESSING"
        COMPLETED = "COMPLETED"
        FAILED = "FAILED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    filename = models.CharField(max_length=255)
    file = models.FileField(upload_to="uploads/%Y/%m/%d/")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PROCESSING
    )
    crs = models.CharField(max_length=64, blank=True)
    feature_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Feature(models.Model):
    class MeasurementStatus(models.TextChoices):
        OK = "OK"
        NOT_REQUIRED = "NOT_REQUIRED"   # e.g. Point
        UNSUPPORTED = "UNSUPPORTED"     # unsupported geometry type
        EMPTY = "EMPTY"                 # null / empty geometry
        ERROR = "ERROR"                 # transform or calculation failure

    file = models.ForeignKey(
        UploadedFile, on_delete=models.CASCADE, related_name="features"
    )
    index = models.PositiveIntegerField()
    layer = models.CharField(max_length=255, blank=True)
    geometry_type = models.CharField(max_length=32)
    geometry = models.JSONField(null=True)     # GeoJSON, in the source CRS
    crs = models.CharField(max_length=64)
    properties = models.JSONField(default=dict)

    area_m2 = models.FloatField(null=True)
    length_m = models.FloatField(null=True)
    projected_crs = models.CharField(max_length=64, blank=True)
    measurement_status = models.CharField(
        max_length=20, choices=MeasurementStatus.choices
    )
    measurement_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["index"]
        constraints = [
            models.UniqueConstraint(
                fields=["file", "index"], name="unique_feature_index_per_file"
            )
        ]
