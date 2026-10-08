from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import UploadedFile
from .serializers import FeatureMeasurementSerializer, FileSerializer, UploadSerializer
from .services.processing import process_file


class FileUploadView(APIView):
    def post(self, request):
        ser = UploadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        upload = ser.validated_data["file"]

        obj = UploadedFile.objects.create(filename=upload.name, file=upload)
        process_file(obj)

        http_status = (
            status.HTTP_201_CREATED
            if obj.status == UploadedFile.Status.COMPLETED
            else status.HTTP_422_UNPROCESSABLE_ENTITY
        )
        return Response(FileSerializer(obj).data, status=http_status)


class FileDetailView(APIView):
    def get(self, request, pk):
        obj = get_object_or_404(UploadedFile, pk=pk)
        return Response(FileSerializer(obj).data)


class MeasurementsView(APIView):
    def get(self, request, pk):
        obj = get_object_or_404(UploadedFile, pk=pk)
        if obj.status != UploadedFile.Status.COMPLETED:
            return Response(
                {"detail": f"File status is {obj.status}.", "error": obj.error or None},
                status=status.HTTP_409_CONFLICT,
            )

        qs = obj.features.all()
        gtype = request.query_params.get("geometry_type")
        if gtype:
            qs = qs.filter(geometry_type__iexact=gtype)

        totals = qs.aggregate(
            total_area_m2=Sum("area_m2"), total_length_m=Sum("length_m"), count=Count("id")
        )
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return Response(
            {
                "file_id": str(obj.id),
                "filename": obj.filename,
                "crs": obj.crs,
                "summary": totals,
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "features": FeatureMeasurementSerializer(page, many=True).data,
            }
        )
