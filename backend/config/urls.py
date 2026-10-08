from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("notificaciones/", include("apps.notifications.urls")),
    path("grupos/", include("apps.groups.urls")),
    path("planes/", include("apps.plans.urls")),
    path("", RedirectView.as_view(pattern_name="users:dashboard", permanent=False)),
    path("", include("apps.users.urls")),
]
