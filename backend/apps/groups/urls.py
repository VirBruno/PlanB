from django.urls import path
from apps.plans import views as plan_views
from . import views

app_name = "groups"
urlpatterns = [
    path("", views.index, name="index"),
    path("nuevo/", views.create, name="create"),
    path("<uuid:group_id>/invitar/", views.invite, name="invite"),
    path("<uuid:group_id>/editar/", views.edit, name="edit"),
    path("<uuid:group_id>/eliminar/confirmar/", views.delete_confirmation, name="delete_confirmation"),
    path("<uuid:group_id>/eliminar/", views.delete, name="delete"),
    path("<uuid:group_id>/planes/", plan_views.group_index, name="plans"),
    path("<uuid:group_id>/planes/nuevo/", plan_views.create, name="plan_create"),
    path("<uuid:group_id>/", views.detail, name="detail"),
]
