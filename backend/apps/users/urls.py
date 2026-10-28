from django.urls import path

from . import calendar_views, views


app_name = "users"

urlpatterns = [
    path("calendario/", calendar_views.index, name="calendar"),
    path('calendario/selector/', calendar_views.selector, name='calendar_selector'),
    path("registro/", views.register, name="register"),
    path("login/", views.login, name="login"),
    path("auth/confirmar-email/", views.email_confirmation, name="email_confirmation"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("perfil/", views.profile, name="profile"),
    path("logout/", views.logout, name="logout"),
]
