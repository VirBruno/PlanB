from django.test import SimpleTestCase
from apps.groups.forms import GroupForm


class GroupFormTests(SimpleTestCase):
    def test_required_name(self):
        for value in ("", " ", "\t\n", "\u00a0"):
            self.assertFalse(GroupForm({"name": value}).is_valid())

    def test_boundaries_and_optional_description(self):
        form = GroupForm({"name": "a" * 100, "description": "d" * 1000})
        self.assertTrue(form.is_valid())
        self.assertFalse(GroupForm({"name": "a" * 101}).is_valid())
        self.assertFalse(GroupForm({"name": "Grupo", "description": "d" * 1001}).is_valid())
        self.assertTrue(GroupForm({"name": "A"}).is_valid())

    def test_unicode_and_trim(self):
        form = GroupForm({"name": "  Salidas 🍕  ", "description": "  Córdoba\n finde  "})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data, {"name": "Salidas 🍕", "description": "Córdoba\n finde"})

    def test_identity_and_role_are_not_form_fields(self):
        form = GroupForm({"name": "Grupo", "created_by": "other", "user_id": "other", "role": "owner"})
        self.assertTrue(form.is_valid())
        self.assertEqual(set(form.cleaned_data), {"name", "description"})

    def test_fields_have_labels_and_description_limit(self):
        html = GroupForm().as_p()
        self.assertIn('for="id_description"', html)
        self.assertIn('maxlength="1000"', html)
