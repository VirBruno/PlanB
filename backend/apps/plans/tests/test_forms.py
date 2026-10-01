from django.test import SimpleTestCase

from apps.plans.forms import PlanForm


class PlanFormTests(SimpleTestCase):
    def test_required_fields_and_boolean_status(self):
        form = PlanForm({
            "name": "Escapada", "description": "Viaje de fin de semana",
            "status": "false",
        })
        self.assertTrue(form.is_valid())
        self.assertFalse(form.cleaned_data["status"])
        self.assertEqual(set(form.cleaned_data), {"name", "description", "status"})

    def test_blank_name_and_description_are_rejected(self):
        form = PlanForm({"name": "  ", "description": "", "status": "true"})
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)
        self.assertIn("description", form.errors)