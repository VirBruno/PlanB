"""El selector comparte datos, permisos, feriados y estados con el calendario."""
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.groups.services.exceptions import GroupNotFound
from apps.plans.services.exceptions import PlanUnavailable
from apps.users.services import calendar_service
from apps.users.services.exceptions import SessionExpired
from apps.users.tests.test_calendar import event, meeting, FIRST, TODAY, USER_ID, GROUP_ID, FOREIGN_GROUP, SECOND_PLAN
from apps.users.tests.test_calendar_holidays import CalendarMarkup


class CalendarSelectorTests(TestCase):
    def mock(self, name, **kwargs):
        started = patch(name, **kwargs)
        result = started.start()
        self.addCleanup(started.stop)
        return result

    def setUp(self):
        self.current = self.mock('apps.users.services.session_service.current_user',
                                 return_value={'id': USER_ID, 'username': 'Ana'})
        self.mock('apps.users.services.session_service.get_access_token', return_value='selector-jwt')
        self.badge = self.mock('apps.notifications.services.unread_count', return_value=0)
        self.mock('apps.users.calendar_views.timezone.localdate', return_value=TODAY)
        self.real_events = calendar_service.list_events
        self.events = self.mock('apps.users.calendar_views.calendar_service.list_events', return_value=[])

    def test_private_partial_has_no_shell_badge_or_plan_links(self):
        self.events.return_value = [event()]
        response = self.client.get('/calendario/selector/')
        self.assertContains(response, 'pb-calendar--selector')
        self.assertContains(response, '15:30')
        self.assertContains(response, 'Mates en el parque')
        self.assertNotContains(response, '<html')
        self.assertNotContains(response, 'href="/planes/')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.events.assert_called_once_with('selector-jwt', FIRST)
        self.badge.assert_not_called()

    def test_month_navigation_selection_and_today_are_buttons(self):
        response = self.client.get('/calendario/selector/?year=2026&month=11&selected=2026-11-20')
        markup = CalendarMarkup(response.content.decode())
        self.assertContains(response, 'Noviembre 2026')
        self.assertContains(response, 'data-calendar-month="2026-10"')
        self.assertContains(response, 'data-calendar-month="2026-12"')
        self.assertContains(response, 'data-calendar-month=""')
        self.assertIn('is-selected', markup.days['2026-11-20'][0]['class'])
        self.assertEqual(markup.days['2026-11-20'][0]['aria-pressed'], 'true')
        self.assertEqual(markup.days['2026-11-21'][0]['aria-pressed'], 'false')
        self.assertContains(response, 'type="button"')
        self.assertNotContains(response, 'type="submit"')
        self.events.assert_called_once_with('selector-jwt', date(2026, 11, 1))

    def test_year_navigation_and_limits_match_main_calendar(self):
        response = self.client.get('/calendario/selector/?year=2026&month=12')
        self.assertContains(response, 'data-calendar-month="2027-01"')
        response = self.client.get('/calendario/selector/?year=1900&month=1')
        self.assertNotContains(response, 'data-calendar-month="1899-12"')
        response = self.client.get('/calendario/selector/?year=2100&month=12')
        self.assertNotContains(response, 'data-calendar-month="2101-01"')

    def test_invalid_month_and_selected_date_are_safe(self):
        for params in ({'year': 'bad', 'selected': 'not-a-date'},
                       {'year': '2101', 'month': '13', 'selected': '2026-02-30'}):
            response = self.client.get('/calendario/selector/', params)
            self.assertContains(response, 'Octubre 2026')
            self.assertNotContains(response, 'aria-pressed="true"')

    def test_holiday_weekend_and_events_coexist_with_full_time(self):
        self.events.return_value = [event(starts_at=event()['starts_at'].replace(month=6, day=20))]
        response = self.client.get('/calendario/selector/?year=2026&month=6')
        day = CalendarMarkup(response.content.decode()).days['2026-06-20'][0]
        self.assertIn('is-weekend', day['class'])
        self.assertIn('is-holiday', day['class'])
        self.assertIn('Belgrano', day['aria-label'])
        self.assertIn('15:30', day['aria-label'])
        self.assertContains(response, 'pb-calendar-holiday-badge')
        self.assertContains(response, 'pb-calendar-event--confirmed')
        self.assertContains(response, 'data-calendar-select="2026-06-20"')

    def test_today_and_current_selection_can_coexist(self):
        response = self.client.get('/calendario/selector/?selected=2026-10-26')
        day = CalendarMarkup(response.content.decode()).days['2026-10-26'][0]
        self.assertIn('is-today', day['class'])
        self.assertIn('is-selected', day['class'])
        self.assertContains(response, 'aria-current="date"')

    def test_dense_days_are_capped_but_all_events_are_in_agenda(self):
        self.events.return_value = [event(name=f'Encuentro {i}') for i in range(15)]
        response = self.client.get('/calendario/selector/')
        self.assertContains(response, '+13 más')
        self.assertContains(response, 'Ver todos los encuentros del mes (15)')
        self.assertContains(response, 'Encuentro 14')
        self.assertNotContains(response, '<a ')

    def test_unavailable_is_not_a_false_empty_available_month(self):
        self.events.side_effect = PlanUnavailable()
        response = self.client.get('/calendario/selector/')
        self.assertContains(response, 'No pudimos cargar tus encuentros', status_code=503)
        self.assertNotContains(response, 'No tenés encuentros', status_code=503)
        self.assertNotContains(response, 'data-calendar-select=', status_code=503)

    def test_anonymous_and_expired_sessions_cannot_get_private_data(self):
        self.current.return_value = None
        response = self.client.get('/calendario/selector/')
        self.assertTrue(response.url.startswith('/login/?next='))
        self.events.assert_not_called()
        self.current.return_value = {'id': USER_ID, 'username': 'Ana'}
        self.events.side_effect = SessionExpired()
        with patch('apps.users.services.session_service.invalidate_session') as invalidate:
            response = self.client.get('/calendario/selector/')
        self.assertTrue(response.url.startswith('/login/?next='))
        invalidate.assert_called_once()

    def test_post_is_rejected(self):
        self.assertEqual(self.client.post('/calendario/selector/').status_code, 405)
        self.events.assert_not_called()

    def test_event_names_are_escaped(self):
        self.events.return_value = [event(name='<img src=x onerror=alert(1)>')]
        response = self.client.get('/calendario/selector/')
        self.assertContains(response, '&lt;img')
        self.assertNotContains(response, '<img src=x')

    def test_real_service_preserves_timezone_permissions_and_query_count(self):
        self.events.side_effect = self.real_events
        organizing = meeting(name='Organizando')
        organizing['selected_proposal_id'] = None
        plans = [meeting(name='Permitido', scheduled_at='2026-10-27T00:30:00Z'),
                 meeting(id=SECOND_PLAN, name='Historial', status=False),
                 meeting(group_id=FOREIGN_GROUP, name='Secreto'), organizing]
        with patch.object(calendar_service.plan_service, 'list_meetings', return_value=plans) as query, \
             patch.object(calendar_service.group_service, 'get_group', side_effect=[
                 {'id': GROUP_ID, 'name': 'Amigos'}, GroupNotFound()]) as groups, \
             timezone.override('America/Argentina/Buenos_Aires'):
            response = self.client.get('/calendario/selector/', {'user_id': 'forged'})
        self.assertContains(response, 'Permitido')
        self.assertContains(response, 'Historial')
        self.assertContains(response, '21:30')
        self.assertNotContains(response, 'Secreto')
        self.assertNotContains(response, 'Organizando')
        query.assert_called_once()
        self.assertEqual(query.call_args.args[0], 'selector-jwt')
        self.assertEqual(groups.call_count, 2)
        self.badge.assert_not_called()
