from django.test import SimpleTestCase
from decimal import Decimal

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

    def test_budget_is_optional_and_empty_values_are_none(self):
        for data in (self.proposal_data(), self.proposal_data(budget_min="", budget_max="")):
            form = ProposalForm(data)
            self.assertTrue(form.is_valid(), form.errors)
            self.assertIsNone(form.cleaned_data["budget_min"])
            self.assertIsNone(form.cleaned_data["budget_max"])

    def test_budget_accepts_partial_ranges_zero_and_exact_decimals(self):
        for minimum, maximum in (("10", "20"), ("10", ""), ("", "20"),
                                 ("0", ""), ("", "0"), ("0", "0"),
                                 ("1.25", "2.75"), ("9999999999.99", "9999999999.99")):
            with self.subTest(minimum=minimum, maximum=maximum):
                form = ProposalForm(self.proposal_data(budget_min=minimum, budget_max=maximum))
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["budget_min"], Decimal(minimum) if minimum else None)
                self.assertEqual(form.cleaned_data["budget_max"], Decimal(maximum) if maximum else None)

    def test_budget_rejects_negative_values_in_either_field(self):
        for field in ("budget_min", "budget_max"):
            form = ProposalForm(self.proposal_data(**{field: "-0.01"}))
            self.assertFalse(form.is_valid())
            self.assertIn(field, form.errors)

    def test_budget_minimum_cannot_exceed_maximum(self):
        form = ProposalForm(self.proposal_data(budget_min="20", budget_max="10"))
        self.assertFalse(form.is_valid())
        self.assertIn("budget_max", form.errors)

    def test_invalid_budget_is_not_silently_rounded_or_normalized(self):
        for field in ("budget_min", "budget_max"):
            for value in ("1.234", "10000000000", "$ 1000", "1,25", "abc", "NaN", "Infinity", "-Infinity"):
                with self.subTest(field=field, value=value):
                    form = ProposalForm(self.proposal_data(**{field: value}))
                    self.assertFalse(form.is_valid())
                    self.assertIn(field, form.errors)
