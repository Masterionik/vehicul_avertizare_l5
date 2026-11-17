from django.urls import include, path

urlpatterns = [
    path("", include("web.page_urls")),
    path("api/", include("web.urls")),
]
