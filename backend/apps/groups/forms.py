from django import forms


class InviteSearchForm(forms.Form):
    username = forms.RegexField(
        regex=r'^[A-Za-z0-9][A-Za-z0-9_.-]{2,29}$', min_length=3, max_length=30,
        label='Username', strip=True,
        error_messages={'invalid': 'Ingresá un username completo y válido.'},
        widget=forms.TextInput(attrs={'autocomplete': 'off', 'placeholder': 'Username completo'}),
    )


class InviteForm(forms.Form):
    invited_user_id = forms.UUIDField(widget=forms.HiddenInput)


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
