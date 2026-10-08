from django.urls import path
from . import views

app_name = 'notifications'
urlpatterns = [
    path('', views.index, name='index'),
    path('<uuid:notification_id>/leida/', views.mark_read, name='mark_read'),
    path('invitaciones/<uuid:invitation_id>/aceptar/', views.accept, name='accept'),
    path('invitaciones/<uuid:invitation_id>/rechazar/', views.reject, name='reject'),
]
