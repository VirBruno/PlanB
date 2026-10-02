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
