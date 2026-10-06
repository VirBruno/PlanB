from django.urls import path

from . import views

app_name = "plans"
urlpatterns = [
    path("", views.index, name="index"),
    path("<uuid:plan_id>/propuestas/nueva/", views.proposal_create, name="proposal_create"),
    path("<uuid:plan_id>/propuestas/<int:proposal_id>/editar/", views.proposal_edit, name="proposal_edit"),
    path("<uuid:plan_id>/propuestas/<int:proposal_id>/eliminar/", views.proposal_delete, name="proposal_delete"),
    path("<uuid:plan_id>/editar/", views.edit, name="edit"),
    path("<uuid:plan_id>/eliminar/confirmar/", views.delete_confirmation, name="delete_confirmation"),
    path("<uuid:plan_id>/eliminar/", views.delete, name="delete"),
    path("<uuid:plan_id>/", views.detail, name="detail"),
]