from django import forms


class GroupForm(forms.Form):
    name = forms.CharField(
        label="Nombre del grupo", max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "Por ejemplo, Escapadas de finde"}),
    )
    description = forms.CharField(
        label="Descripción (opcional)", required=False, max_length=1000,
        help_text="Hasta 1.000 caracteres.",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "¿Qué planes les gustaría compartir?"}),
    )
