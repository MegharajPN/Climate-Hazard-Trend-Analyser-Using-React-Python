from django.urls import path

from hazards import views

urlpatterns = [
    path("api/health/", views.health),
    path("api/geocode/", views.geocode),
    path("api/analysis/", views.analysis),
    path("api/analysis/export/", views.analysis_csv),
]
