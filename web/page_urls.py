from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("", views.page_dashboard, name="index"),
    path("devices/", views.page_devices, name="devices"),
    path("simulation/", views.page_simulation, name="simulation"),
    path("tests/", views.page_tests, name="tests"),
]
