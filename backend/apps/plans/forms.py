import json
import math

from django import forms


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

    def clean_status(self):
        return self.cleaned_data["status"] == "true"


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
    date_pick = forms.DateTimeField(
        label="Fecha y hora",
        required=False,
        input_formats=("%Y-%m-%dT%H:%M",),
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"},
        ),
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
