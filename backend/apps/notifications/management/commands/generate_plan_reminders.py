"""Punto de entrada para el scheduler externo o ejecución manual."""
from django.core.management.base import BaseCommand, CommandError

from apps.notifications import reminders


class Command(BaseCommand):
    help = 'Genera recordatorios internos de encuentros confirmados durante las próximas 24 horas.'
    requires_system_checks = []

    def handle(self, *args, **options):
        try:
            count = reminders.generate_plan_reminders()
        except reminders.ReminderUnavailable as error:
            raise CommandError(str(error)) from None
        self.stdout.write(self.style.SUCCESS(f'Recordatorios creados: {count}.'))
