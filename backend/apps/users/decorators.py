"""Protección reutilizable de rutas y redirecciones internas."""
from functools import wraps
from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache

from .services import session_service
from .services.exceptions import ServiceUnavailable, SessionExpired


def supabase_login_required(view):
    @never_cache
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            user = session_service.current_user(request)
        except ServiceUnavailable:
            # Se mantiene el layout público, sin exponer detalles del proveedor.
            from django.shortcuts import render
            return render(request, "users/service_unavailable.html", status=503)
        if user is None:
            target = reverse("users:login") + "?" + urlencode({"next": request.get_full_path()})
            return redirect(target)
        request.planb_user = user
        try:
            return view(request, *args, **kwargs)
        except SessionExpired:
            session_service.invalidate_session(request)
            target = reverse("users:login") + "?" + urlencode({"next": request.get_full_path()})
            return redirect(target)
    return wrapped


def private_page(view):
    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Referrer-Policy"] = "no-referrer"
        return response
    return wrapped
