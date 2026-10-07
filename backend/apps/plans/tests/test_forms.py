from django.test import SimpleTestCase

from apps.plans.forms import PlanForm, ProposalForm


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


class ProposalFormTests(SimpleTestCase):
    def proposal_data(self, **overrides):
        return {
            "tittle": "Merienda en el parque",
            "description": "Llevamos algo para compartir",
            "date_pick": "2026-10-12T17:30",
            "type": "juntada",
            "position": '{"type":"Point","coordinates":[-58.38,-34.6]}',
            **overrides,
        }

    def test_validates_fields_and_geojson_point(self):
        form = ProposalForm(self.proposal_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["position"], {
            "type": "Point", "coordinates": [-58.38, -34.6],
        })
        self.assertEqual(form.cleaned_data["type"], "juntada")

    def test_rejects_missing_or_out_of_range_location_and_unknown_type(self):
        for position in (
            "", '{"type":"Point","coordinates":[181,0]}',
            '{"type":"Point","coordinates":[0,91]}',
        ):
            form = ProposalForm(self.proposal_data(position=position))
            self.assertFalse(form.is_valid())
            self.assertIn("position", form.errors)
        form = ProposalForm(self.proposal_data(type="otro"))
        self.assertFalse(form.is_valid())
        self.assertIn("type", form.errors)

    def test_requires_explicit_proposal_type_selection(self):
        data = self.proposal_data()
        data.pop("type")
        form = ProposalForm(data)
        self.assertFalse(form.is_valid())
        self.assertIn("type", form.errors)
        self.assertEqual(form.fields["type"].initial, "")