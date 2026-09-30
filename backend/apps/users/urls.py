from django.urls import path

from . import views


app_name = "users"

urlpatterns = [
    path("registro/", views.register, name="register"),
    path("login/", views.login, name="login"),
    path("auth/confirmar-email/", views.email_confirmation, name="email_confirmation"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("logout/", views.logout, name="logout"),
]
