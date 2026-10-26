"""Generación programable; la selección y escritura son atómicas en PostgreSQL."""
from django.views.decorators.debug import sensitive_variables

from apps.users.services import clients


class ReminderUnavailable(Exception):
    def __init__(self):
        super().__init__('No se pudo confirmar la generación de recordatorios. Es seguro volver a ejecutar el comando.')


@sensitive_variables()
def generate_plan_reminders():
    """Única operación administrativa; nunca se llama desde requests de usuarios.

    La RPC usa el reloj de PostgreSQL, la ventana (ahora, ahora + 24h] y un
    índice único por usuario/plan/tipo. Devuelve sólo la cantidad insertada.
    """
    try:
        with clients.administrative_client() as client:
            count = client.rpc('generate_plan_reminders', {}).execute().data
        if type(count) is not int or count < 0:
            raise ValueError
        return count
    except Exception:
        raise ReminderUnavailable() from None
