"""Fecha/ubicación persistidas y clima horario; sin realizar llamadas reales."""
from datetime import date, datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from apps.groups.services.exceptions import GroupNotFound
from apps.plans.services import plan_weather_service, plan_service
from apps.plans.tests.test_services import GROUP_ID, PLAN_ID, USER_ID, plan_row
from apps.plans.tests.test_plan_weather import city_record, fake_response


class HourlyWeatherTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    @patch('apps.plans.services.plan_weather_service.timezone.localdate', return_value=date(2026, 10, 21))
    @patch('apps.plans.services.plan_weather_service.httpx.get')
    def test_hourly_weather_at_selected_hour_is_cached(self, get, today):
        payload = {'hourly': {
            'time': [f'2026-10-25T{i:02d}:00' for i in range(24)],
            'temperature_2m': [21.3] * 24,
            'precipitation_probability': [25] * 24,
            'weather_code': [1] * 24,
            'wind_speed_10m': [9.8] * 24,
        }}
        get.side_effect = [fake_response(city_record()), fake_response(payload)]
        result = plan_weather_service.forecast_for_datetime('buenos-aires', 3433955, date(2026, 10, 25), time(15, 30))
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['time'], '15:00')
        self.assertEqual(result['temperature'], 21)
        self.assertEqual(result['rain_probability'], 25)
        self.assertEqual(result, plan_weather_service.forecast_for_datetime('buenos-aires', 3433955, date(2026, 10, 25), time(15, 30)))
        self.assertEqual(get.call_count, 2)
        self.assertIn('hourly', get.call_args.kwargs['params'])

    @patch('apps.plans.services.plan_weather_service.timezone.localdate', return_value=date(2026, 10, 21))
    @patch('apps.plans.services.plan_weather_service.httpx.get')
    def test_far_away_or_past_does_not_query_forecast(self, get, today):
        self.assertEqual(plan_weather_service.forecast_for_datetime('buenos-aires', 3433955, date(2026, 12, 5), time(16, 0))['status'], 'too_early')
        self.assertEqual(plan_weather_service.forecast_for_datetime('buenos-aires', 3433955, date(2026, 10, 10), time(16, 0))['status'], 'past')
        get.assert_not_called()

    @patch('apps.plans.services.plan_weather_service.httpx.get')
    def test_selected_city_is_verified_in_requested_province(self, get):
        get.return_value = fake_response(city_record(admin1='Córdoba'))
        with self.assertRaises(ValueError):
            plan_weather_service.resolve_city('buenos-aires', 3433955)
        get.assert_called_once()


class PlanServiceScheduleTests(SimpleTestCase):
    def test_scheduling_payload_is_complete_and_aware(self):
        meeting = datetime(2026, 10, 25, 15, 30, tzinfo=ZoneInfo('America/Argentina/Buenos_Aires'))
        payload = plan_service._planning_payload(meeting, 'buenos-aires', 3433955, 'Mar del Plata')
        self.assertEqual(payload['scheduled_at'], '2026-10-25T15:30:00-03:00')
        self.assertEqual(payload['weather_city_id'], 3433955)
        self.assertEqual(plan_service._planning_payload(None, None, None, None)['scheduled_at'], None)

    def test_plan_row_exposes_optional_fields_without_breaking_old_data(self):
        old = plan_service._plan(plan_row())
        self.assertFalse(old['planning_schema'])
        self.assertIsNone(old['scheduled_at'])
        newer = plan_service._plan(plan_row(
            scheduled_at='2026-10-25T18:30:00+00:00',
            weather_province='buenos-aires', weather_city_id=3433955,
            weather_city_name='Mar del Plata'))
        self.assertTrue(newer['planning_schema'])
        self.assertEqual(newer['scheduled_at'].hour, 18)


class PlanScheduleViewTests(TestCase):
    def setUp(self):
        self.current = patch('apps.users.services.session_service.current_user', return_value={'id': USER_ID, 'username': 'Ana'})
        self.token = patch('apps.users.services.session_service.get_access_token', return_value='private-jwt')
        self.current.start(); self.token.start()
        self.addCleanup(self.current.stop); self.addCleanup(self.token.stop)

    @patch('apps.plans.views.group_service.get_group', return_value={'id': GROUP_ID, 'name': 'Grupo', 'role': 'member'})
    def test_create_form_has_schedule_and_forecast(self, group):
        response = self.client.get(f'/grupos/{GROUP_ID}/planes/nuevo/')
        self.assertContains(response, 'data-plan-schedule-form')
        self.assertContains(response, 'name="plan_date"')
        self.assertContains(response, 'name="plan_time"')
        self.assertContains(response, 'name="weather_city_id"')

    @patch('apps.plans.views.plan_service.create_plan', return_value=PLAN_ID)
    @patch('apps.plans.views.plan_weather_service.resolve_city', return_value={'name': 'Mar del Plata'})
    @patch('apps.plans.views.group_service.get_group', return_value={'id': GROUP_ID, 'name': 'Grupo', 'role': 'member'})
    def test_member_creates_plan_with_validated_city_and_schedule(self, group, resolve, create):
        response = self.client.post(f'/grupos/{GROUP_ID}/planes/nuevo/', {
            'name': 'Picnic', 'description': 'Parque', 'status': 'true',
            'plan_date': '2026-10-25', 'plan_time': '15:30',
            'weather_province': 'buenos-aires', 'weather_city_id': '3433955',
        })
        self.assertEqual(response.status_code, 302)
        resolve.assert_called_once_with('buenos-aires', 3433955)
        self.assertEqual(create.call_args.kwargs['weather_city_name'], 'Mar del Plata')
        self.assertEqual(create.call_args.kwargs['scheduled_at'].hour, 15)

    @patch('apps.plans.views.plan_service.create_plan')
    @patch('apps.plans.views.plan_weather_service.resolve_city')
    @patch('apps.plans.views.group_service.get_group', return_value={'id': GROUP_ID, 'name': 'Grupo', 'role': 'member'})
    def test_reject_incomplete_location_and_date(self, group, resolve, create):
        response = self.client.post(f'/grupos/{GROUP_ID}/planes/nuevo/', {
            'name': 'Picnic', 'description': 'Parque', 'status': 'true', 'plan_date': '2026-10-25',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Completá la fecha, hora, provincia y localidad')
        create.assert_not_called(); resolve.assert_not_called()

    @patch('apps.plans.views.plan_weather_service.forecast_for_datetime', return_value={'status': 'available', 'temperature': 23})
    @patch('apps.plans.views.group_service.get_group', return_value={'id': GROUP_ID, 'role': 'member'})
    def test_weather_endpoint_returns_scheduled_hour(self, group, forecast):
        response = self.client.get(f'/planes/grupo/{GROUP_ID}/clima/horario/', {
            'provincia': 'buenos-aires', 'localidad_id': '3433955', 'fecha': '2026-10-25', 'hora': '15:30',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['temperature'], 23)
        self.assertEqual(forecast.call_args.args[3].hour, 15)

    @patch('apps.plans.views.plan_weather_service.forecast_for_datetime')
    @patch('apps.plans.views.group_service.get_group', side_effect=GroupNotFound())
    def test_outsider_cannot_consult_weather(self, group, forecast):
        response = self.client.get(f'/planes/grupo/{GROUP_ID}/clima/horario/', {
            'provincia': 'buenos-aires', 'localidad_id': '3433955', 'fecha': '2026-10-25', 'hora': '15:30',
        })
        self.assertEqual(response.status_code, 404)
        forecast.assert_not_called()
