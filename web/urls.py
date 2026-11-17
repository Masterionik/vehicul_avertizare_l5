from django.urls import path

from . import views

urlpatterns = [
    path("state/", views.api_state, name="api_state"),
    path("pulse/<str:output>/", views.api_pulse, name="api_pulse"),
    path("outputs/off/", views.api_outputs_off, name="api_outputs_off"),
    path("distance/read/", views.api_distance_read, name="api_distance_read"),
    path("mesh/test/", views.api_mesh_test, name="api_mesh_test"),
    path("sim/distance/", views.api_sim_distance, name="api_sim_distance"),
    path("sim/vibration/", views.api_sim_vibration, name="api_sim_vibration"),
    path("sim/remote-alert/", views.api_sim_remote_alert, name="api_sim_remote_alert"),
    path("tests/", views.api_tests_list, name="api_tests_list"),
    path("tests/run/", views.api_tests_run, name="api_tests_run"),
    path("devices/", views.api_devices, name="api_devices"),
    path("devices/scan/", views.api_devices_scan, name="api_devices_scan"),
    path("devices/add/", views.api_devices_add, name="api_devices_add"),
    path("devices/<str:device_id>/<str:action>/", views.api_device_action, name="api_device_action"),
]
