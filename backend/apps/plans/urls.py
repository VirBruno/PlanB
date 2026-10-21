from django.urls import path

from . import views

app_name = "plans"
urlpatterns = [
    path("", views.index, name="index"),
    path("grupo/<uuid:group_id>/clima/localidades/", views.group_weather_cities, name="group_weather_cities"),
    path("grupo/<uuid:group_id>/clima/horario/", views.group_weather_at, name="group_weather_at"),
    path("<uuid:plan_id>/clima/localidades/", views.plan_weather_cities, name="plan_weather_cities"),
    path("<uuid:plan_id>/clima/pronostico/", views.plan_weather_forecast, name="plan_weather_forecast"),
    path("<uuid:plan_id>/propuestas/metodo/", views.proposal_election_method, name="proposal_election_method"),
    path("<uuid:plan_id>/propuestas/<int:proposal_id>/clima/", views.proposal_weather, name="proposal_weather"),
    path("<uuid:plan_id>/propuestas/ideal/", views.proposal_ideal, name="proposal_ideal"),
    path("<uuid:plan_id>/propuestas/voto/eliminar/", views.proposal_vote_remove, name="proposal_vote_remove"),
    path("<uuid:plan_id>/propuestas/<int:proposal_id>/votar/", views.proposal_vote, name="proposal_vote"),
    path("<uuid:plan_id>/propuestas/<int:proposal_id>/score/", views.proposal_score, name="proposal_score"),
    path("<uuid:plan_id>/propuestas/nueva/", views.proposal_create, name="proposal_create"),
    path("<uuid:plan_id>/propuestas/<int:proposal_id>/editar/", views.proposal_edit, name="proposal_edit"),
    path("<uuid:plan_id>/propuestas/<int:proposal_id>/eliminar/", views.proposal_delete, name="proposal_delete"),
    path("<uuid:plan_id>/editar/", views.edit, name="edit"),
    path("<uuid:plan_id>/eliminar/confirmar/", views.delete_confirmation, name="delete_confirmation"),
    path("<uuid:plan_id>/eliminar/", views.delete, name="delete"),
    path("<uuid:plan_id>/", views.detail, name="detail"),
]