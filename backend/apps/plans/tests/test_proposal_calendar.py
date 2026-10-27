"""Inputs separados y el mismo date_pick final en creación y edición."""
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.plans.forms import ProposalForm
from apps.plans.tests.test_services import plan_row, proposal_row, USER_ID, PLAN_ID, GROUP_ID


def data(**values):
    return {'tittle': 'Mates', 'description': '', 'type': 'juntada',
            'position': '{"type":"Point","coordinates":[-58.38,-34.6]}',
            'date_pick_0': '2026-11-20', 'date_pick_1': '21:30', **values}


class ProposalSplitDateTests(SimpleTestCase):
    def test_split_fields_combine_in_current_timezone(self):
        with timezone.override('America/Argentina/Buenos_Aires'):
            form = ProposalForm(data())
            self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['date_pick'].isoformat(), '2026-11-20T21:30:00-03:00')

    def test_date_without_hour_remains_blank_and_requires_explicit_hour(self):
        form = ProposalForm(data(date_pick_1=''))
        self.assertFalse(form.is_valid())
        self.assertIn('date_pick', form.errors)
        self.assertEqual(form['date_pick'].value(), ['2026-11-20', ''])

    def test_hour_without_date_does_not_invent_date(self):
        form = ProposalForm(data(date_pick_0=''))
        self.assertFalse(form.is_valid())
        self.assertIn('date_pick', form.errors)
        self.assertEqual(form['date_pick'].value(), ['', '21:30'])

    def test_both_blank_keep_optional_date_pick_none(self):
        form = ProposalForm(data(date_pick_0='', date_pick_1=''))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['date_pick'])

    def test_legacy_combined_post_is_still_accepted(self):
        values = data()
        del values['date_pick_0'], values['date_pick_1']
        form = ProposalForm(values | {'date_pick': '2026-11-20T21:30'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['date_pick'].strftime('%Y-%m-%d %H:%M'), '2026-11-20 21:30')

    def test_split_post_takes_precedence_over_stale_combined_value(self):
        form = ProposalForm(data(date_pick='2020-01-01T00:00'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['date_pick'].strftime('%Y-%m-%d %H:%M'), '2026-11-20 21:30')

    def test_invalid_date_and_time_are_rejected(self):
        for values in ({'date_pick_0': '2026-02-30'}, {'date_pick_1': '25:30'}):
            form = ProposalForm(data(**values))
            self.assertFalse(form.is_valid())
            self.assertIn('date_pick', form.errors)


class ProposalCalendarViewsTests(TestCase):
    def mock(self, name, **kwargs):
        started = patch(name, **kwargs)
        result = started.start()
        self.addCleanup(started.stop)
        return result

    def setUp(self):
        self.mock('apps.users.services.session_service.current_user', return_value={'id': USER_ID, 'username': 'Ana'})
        self.mock('apps.users.services.session_service.get_access_token', return_value='jwt')
        self.mock('apps.notifications.services.unread_count', return_value=0)
        self.plan = plan_row()
        self.mock('apps.plans.views.plan_service.get_plan', return_value=self.plan)
        self.mock('apps.plans.views.group_service.get_group', return_value={'id': GROUP_ID, 'name': 'Amigos', 'role': 'member'})
        self.mock('apps.plans.views.proposal_service.has_user_proposal', return_value=False)
        self.proposal = proposal_row(date_pick=datetime(2026, 11, 15, 0, 30, tzinfo=dt_timezone.utc))
        self.mock('apps.plans.views.proposal_service.get_proposal', return_value=self.proposal)
        self.create = self.mock('apps.plans.views.proposal_service.create_proposal', return_value=47)
        self.update = self.mock('apps.plans.views.proposal_service.update_proposal', return_value=47)
        self.events = self.mock('apps.users.services.calendar_service.list_events', return_value=[])
        self.new_url = f'/planes/{PLAN_ID}/propuestas/nueva/'
        self.edit_url = f'/planes/{PLAN_ID}/propuestas/47/editar/'

    def test_create_and_edit_show_same_modal_manual_inputs_and_shared_styles(self):
        for url in (self.new_url, self.edit_url):
            response = self.client.get(url)
            self.assertContains(response, 'Elegir fecha desde mi calendario')
            self.assertContains(response, 'name="date_pick_0"')
            self.assertContains(response, 'name="date_pick_1"')
            self.assertContains(response, 'type="date"')
            self.assertContains(response, 'type="time"')
            self.assertContains(response, '<dialog')
            self.assertContains(response, 'data-calendar-url="/calendario/selector/"')
            self.assertContains(response, 'personal-calendar.css')
            self.assertContains(response, 'proposal-calendar.css')
            self.assertContains(response, 'proposal-calendar.js')
        self.events.assert_not_called()  # Se consulta recién al abrir el modal.

    def test_edit_decompresses_existing_timestamp_to_local_date_and_hour(self):
        with timezone.override('America/Argentina/Buenos_Aires'):
            response = self.client.get(self.edit_url)
        self.assertContains(response, 'value="2026-11-14"')
        self.assertContains(response, 'value="21:30"')

    def test_create_and_edit_submit_same_combined_timestamp(self):
        for url, operation in ((self.new_url, self.create), (self.edit_url, self.update)):
            response = self.client.post(url, data())
            self.assertEqual(response.status_code, 302)
            self.assertEqual(operation.call_args.kwargs['date_pick'].isoformat(), '2026-11-20T21:30:00-03:00')

    def test_invalid_submission_preserves_both_inputs_without_writing(self):
        for url in (self.new_url, self.edit_url):
            response = self.client.post(url, data(tittle=''))
            self.assertContains(response, 'value="2026-11-20"')
            self.assertContains(response, 'value="21:30"')
        self.create.assert_not_called()
        self.update.assert_not_called()

    def test_missing_hour_keeps_it_empty_and_does_not_save(self):
        response = self.client.post(self.new_url, data(date_pick_1=''))
        self.assertContains(response, 'value="2026-11-20"')
        self.assertNotContains(response, 'value="00:00"')
        self.create.assert_not_called()

    def test_other_author_and_confirmed_plan_still_cannot_edit(self):
        self.proposal['created_by'] = 'e0c9b706-c892-4f13-8432-86ce10caf447'
        self.assertEqual(self.client.get(self.edit_url).status_code, 403)
        self.proposal['created_by'] = USER_ID
        self.plan['selected_proposal_id'] = 47
        self.assertEqual(self.client.get(self.edit_url).status_code, 403)
        self.update.assert_not_called()
