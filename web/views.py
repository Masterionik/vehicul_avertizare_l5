"""Thin Django views: parse the request, call ``web.api``, return JSON or a page. No hardware access here."""

import json

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from . import api


def _params(request):
    """Request parameters from a JSON body or a form post."""
    if request.content_type == "application/json":
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    return request.POST.dict()


def _json(result):
    status, payload = result
    return JsonResponse(payload, status=status)


# ----------------------------------------------------------------------------- pages
@require_GET
def page_dashboard(request):
    return render(request, "web/dashboard.html", {"page": "dashboard"})


@require_GET
def page_devices(request):
    return render(request, "web/devices.html", {"page": "devices"})


@require_GET
def page_simulation(request):
    return render(request, "web/simulation.html", {"page": "simulation"})


@require_GET
def page_tests(request):
    return render(request, "web/tests.html", {"page": "tests"})


# ------------------------------------------------------------------------------ API
@require_GET
def api_state(request):
    return _json((200, api.state()))


@require_POST
def api_pulse(request, output):
    return _json(api.pulse(output, _params(request).get("duration_ms")))


@require_POST
def api_outputs_off(request):
    return _json(api.outputs_off())


@require_POST
def api_distance_read(request):
    return _json(api.distance_read())


@require_POST
def api_mesh_test(request):
    return _json(api.mesh_test())


@require_POST
def api_sim_distance(request):
    return _json(api.sim_distance(_params(request).get("distance_cm")))


@require_POST
def api_sim_vibration(request):
    return _json(api.sim_vibration(_params(request).get("count")))


@require_POST
def api_sim_remote_alert(request):
    p = _params(request)
    return _json(api.sim_remote_alert(p.get("vehicle", ""), p.get("script", ""), p.get("radio", "mesh")))


@require_GET
def api_tests_list(request):
    return _json(api.tests_list())


@require_POST
def api_tests_run(request):
    return _json(api.tests_run(str(_params(request).get("name", "all"))))


@require_GET
def api_devices(request):
    return _json(api.devices_state())


@require_POST
def api_devices_scan(request):
    return _json(api.devices_scan())


@require_POST
def api_devices_add(request):
    p = _params(request)
    return _json(api.devices_add_tcp(p.get("host", ""), p.get("port")))


@require_POST
def api_device_action(request, device_id, action):
    return _json(api.device_action(device_id, action, api.parse_confirm(_params(request).get("confirm"))))
