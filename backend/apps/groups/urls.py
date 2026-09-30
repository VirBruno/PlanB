from django.urls import path
from . import views

app_name = "groups"
urlpatterns = [
    path("nuevo/", views.create, name="create"),
    path("<uuid:group_id>/editar/", views.edit, name="edit"),
    path("<uuid:group_id>/eliminar/confirmar/", views.delete_confirmation, name="delete_confirmation"),
    path("<uuid:group_id>/eliminar/", views.delete, name="delete"),
    path("<uuid:group_id>/", views.detail, name="detail"),
]
