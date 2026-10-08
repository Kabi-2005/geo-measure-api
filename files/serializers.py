from pathlib import Path

from django.conf import settings
from rest_framework import serializers

from .models import Feature, UploadedFile

ALLOWED_EXTENSIONS = {".zip", ".kml"}


class UploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, f):
        ext = Path(f.name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise serializers.ValidationError(
                "Unsupported file type. Upload a .zip (Shapefile) or .kml."
            )
        if f.size > settings.MAX_UPLOAD_BYTES:
            raise serializers.ValidationError("File exceeds the maximum upload size.")
        return f


class FileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UploadedFile
        fields = ["id", "filename", "feature_count", "crs", "status", "error", "created_at"]


class FeatureMeasurementSerializer(serializers.ModelSerializer):
    id = serializers.IntegerField(source="index")
    measurements = serializers.SerializerMethodField()

    class Meta:
        model = Feature
        fields = [
            "id", "layer", "geometry_type", "crs", "properties",
            "geometry", "measurements",
        ]

    def get_measurements(self, obj):
        return {
            "status": obj.measurement_status,
            "area_m2": obj.area_m2,
            "length_m": obj.length_m,
            "projected_crs": obj.projected_crs or None,
            "note": obj.measurement_note or None,
        }
