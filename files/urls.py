from django.urls import path

from . import views

urlpatterns = [
    path("files/", views.FileUploadView.as_view(), name="file-upload"),
    path("files/<uuid:pk>/", views.FileDetailView.as_view(), name="file-detail"),
    path(
        "files/<uuid:pk>/measurements/",
        views.MeasurementsView.as_view(),
        name="file-measurements",
    ),
]
