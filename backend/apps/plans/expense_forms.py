import re
from decimal import Decimal

from django import forms

from .services.expense_service import MAX_AMOUNT


class ExpenseAmountField(forms.DecimalField):
    """Entrada argentina: 30.000 / 30.000,50; también 30000.50 sin separadores."""
    def to_python(self, value):
        if isinstance(value, str):
            value = value.strip()
            if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", value):
                value = value.replace(".", "").replace(",", ".")
            elif re.fullmatch(r"\d+(?:,\d{1,2})?", value):
                value = value.replace(",", ".")
            elif not re.fullmatch(r"\d+\.\d{1,2}", value):
                raise forms.ValidationError("Ingresá un monto válido con hasta dos decimales (ej.: 30.000,50).")
        return super().to_python(value)


class ExpenseForm(forms.Form):
    description = forms.CharField(label="Concepto", max_length=160, strip=True)
    amount = ExpenseAmountField(
        label="Monto", min_value=Decimal("0.01"), max_value=MAX_AMOUNT,
        max_digits=14, decimal_places=2,
        widget=forms.TextInput(attrs={"inputmode": "decimal", "placeholder": "Ej.: 30.000,50"}),
    )
    paid_by = forms.ChoiceField(label="Pagó")
    all_group = forms.BooleanField(label="TODO EL GRUPO", required=False)
    participants = forms.MultipleChoiceField(
        label="Se divide entre", required=False, widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, members, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(row["id"], row["username"]) for row in members]
        self.fields["paid_by"].choices = [("", "Elegí quién pagó"), *choices]
        self.fields["participants"].choices = choices

    def clean(self):
        cleaned = super().clean()
        ids = cleaned.get("participants", [])
        if len(ids) != len(set(ids)):
            self.add_error("participants", "No repitas integrantes.")
        if cleaned.get("all_group"):
            # El flag es explícito; la RPC expande la membresía al momento de guardar.
            cleaned["participants"] = []
        elif not ids:
            self.add_error("participants", "Seleccioná al menos un integrante o TODO EL GRUPO.")
        return cleaned
