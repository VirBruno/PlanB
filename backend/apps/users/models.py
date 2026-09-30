"""Infraestructura técnica. Identidad y perfiles pertenecen a Supabase."""
from django.contrib.sessions.models import Session
from django.db import models

class SupabaseSession(models.Model):
    session = models.OneToOneField(Session, primary_key=True, on_delete=models.CASCADE)
    access_token = models.TextField()
    refresh_token = models.TextField()
    expires_at = models.DateTimeField()

    def __str__(self):
        return "Sesión de Supabase"
