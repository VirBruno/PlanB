import json
import math
from decimal import Decimal

from django import forms
from django.utils import timezone
from .services.plan_weather_service import PROVINCES, ARGENTINA_TZ


class PlanForm(forms.Form):
    name = forms.CharField(
        label="Nombre del plan",
        widget=forms.TextInput(attrs={"placeholder": "Por ejemplo, Escapada a la montaña"}),
    )
    description = forms.CharField(
        label="Descripción",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "¿Qué tienen pensado hacer?"}),
    )
    status = forms.ChoiceField(
        label="Estado",
        choices=(("true", "Activo"), ("false", "Inactivo")),
    )

    plan_date = forms.DateField(
        label="Día del plan", required=False,
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    plan_time = forms.TimeField(
        label="Horario", required=False, input_formats=("%H:%M",),
        widget=forms.TimeInput(attrs={"type": "time", "step": "60"}, format="%H:%M"),
    )
    weather_province = forms.ChoiceField(
        label="Provincia", required=False,
        choices=[("", "Elegí una provincia"), *PROVINCES.items()],
    )
    weather_city_id = forms.IntegerField(
        required=False, min_value=1, max_value=2**31 - 1,
        widget=forms.HiddenInput(),
    )

    def clean_status(self):
        return self.cleaned_data["status"] == "true"

    def clean(self):
        cleaned = super().clean()
        fields = ("plan_date", "plan_time", "weather_province", "weather_city_id")
        # Los planes existentes pueden no tener estos datos; si se elige alguno,
        # deben estar todos completos para evitar una ubicación/fecha ambigua.
        if any(cleaned.get(field) is not None and cleaned.get(field) != "" for field in fields):
            for field in fields:
                if not cleaned.get(field):
                    self.add_error(field, "Completá la fecha, hora, provincia y localidad del plan.")
            if not self.errors:
                from datetime import datetime
                naive = datetime.combine(cleaned["plan_date"], cleaned["plan_time"])
                cleaned["scheduled_at"] = timezone.make_aware(naive, ARGENTINA_TZ)
        else:
            cleaned["scheduled_at"] = None
        return cleaned


class ProposalDateTimeWidget(forms.SplitDateTimeWidget):
    """Dos controles visibles; conserva compatibilidad con POSTs del input anterior."""
    template_name = 'plans/widgets/proposal_datetime.html'

    def __init__(self):
        super().__init__(
            date_format='%Y-%m-%d', time_format='%H:%M',
            date_attrs={'type': 'date', 'data-proposal-date': ''},
            time_attrs={'type': 'time', 'step': '60', 'data-proposal-time': ''},
        )

    def value_from_datadict(self, data, files, name):
        if name in data and not any(f'{name}_{i}' in data for i in (0, 1)):
            value = data.get(name, '')
            if isinstance(value, str):
                day, _, hour = value.partition('T')
                return [day, hour]
        return super().value_from_datadict(data, files, name)

    def id_for_label(self, id_):
        return f'{id_}_0' if id_ else ''


class ProposalForm(forms.Form):
    TYPES = (
        ("juntada", "Juntada"),
        ("reunión", "Reunión"),
        ("salida", "Salida"),
    )

    tittle = forms.CharField(
        label="Título",
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "Por ejemplo, Merienda en el parque"}),
    )
    description = forms.CharField(
        label="Descripción",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Contá un poco más de la propuesta"}),
    )
    date_pick = forms.SplitDateTimeField(
        label="Fecha y hora",
        required=False,
        input_date_formats=('%Y-%m-%d',), input_time_formats=('%H:%M',),
        widget=ProposalDateTimeWidget(),
    )
    type = forms.ChoiceField(
        label="Tipo de juntada",
        choices=(("", "Elegí el tipo"), *TYPES),
        initial="",
    )
    position = forms.CharField(widget=forms.HiddenInput())
    city = forms.CharField(
        label="Buscar por ciudad",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Ej.: Buenos Aires"}),
        help_text="Buscá una ciudad y elegí el punto en el mapa.",
    )
    budget_min = forms.DecimalField(
        label="Desde", required=False, min_value=Decimal("0"),
        max_digits=12, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0", "inputmode": "decimal"}),
    )
    budget_max = forms.DecimalField(
        label="Hasta", required=False, min_value=Decimal("0"),
        max_digits=12, decimal_places=2,
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0", "inputmode": "decimal"}),
    )

    def clean(self):
        cleaned = super().clean()
        budget_min, budget_max = cleaned.get("budget_min"), cleaned.get("budget_max")
        if budget_min is not None and budget_max is not None and budget_min > budget_max:
            self.add_error("budget_max", "El presupuesto hasta debe ser mayor o igual al presupuesto desde.")
        return cleaned

    def clean_tittle(self):
        value = self.cleaned_data["tittle"].strip()
        if not value:
            raise forms.ValidationError("Ingresá un título.")
        return value

    def clean_description(self):
        return self.cleaned_data["description"].strip()

    def clean_position(self):
        try:
            position = json.loads(self.cleaned_data["position"])
            coordinates = position["coordinates"]
            if position["type"] != "Point" or len(coordinates) != 2:
                raise ValueError
            longitude, latitude = coordinates
            if (not isinstance(longitude, (int, float))
                    or not isinstance(latitude, (int, float))
                    or isinstance(longitude, bool) or isinstance(latitude, bool)
                    or not math.isfinite(float(longitude))
                    or not math.isfinite(float(latitude))):
                raise ValueError
            longitude, latitude = float(longitude), float(latitude)
            if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise forms.ValidationError("Elegí una ubicación válida en el mapa.") from None
        return {"type": "Point", "coordinates": [longitude, latitude]}
