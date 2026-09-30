from django.urls import path
from . import views

app_name = "groups"
urlpatterns = [
    path("nuevo/", views.create, name="create"),
    path("<uuid:group_id>/", views.detail, name="detail"),
]
