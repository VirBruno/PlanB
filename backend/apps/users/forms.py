"""Validación de entrada sin persistir contraseñas ni crear usuarios Django."""

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .validators import validate_password, validate_username


def text_input(autocomplete, placeholder):
    return forms.TextInput(attrs={
        "autocomplete": autocomplete,
        "placeholder": placeholder,
        "autocapitalize": "none",
        "spellcheck": "false",
    })


def password_input(autocomplete):
    return forms.PasswordInput(attrs={"autocomplete": autocomplete})


class RegisterForm(forms.Form):
    username = forms.CharField(
        label="Nombre de usuario",
        max_length=30,
        validators=[validate_username],
        help_text="3 a 30 caracteres. Letras sin acentos, números, punto, guion o guion bajo.",
        widget=text_input("username", "Tu nombre en Plan B"),
    )
    email = forms.EmailField(
        label="Email",
        max_length=254,
        widget=forms.EmailInput(attrs={
            "autocomplete": "email", "placeholder": "vos@ejemplo.com",
            "autocapitalize": "none", "spellcheck": "false",
        }),
    )
    password = forms.CharField(
        label="Contraseña",
        strip=False,
        validators=[validate_password],
        help_text="Al menos 12 caracteres, una letra (a-z o A-Z) y un número (0-9).",
        widget=password_input("new-password"),
    )
    password_confirm = forms.CharField(
        label="Repetí tu contraseña",
        strip=False,
        widget=password_input("new-password"),
    )

    def clean_email(self):
        return self.cleaned_data["email"].lower()

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        confirmation = cleaned.get("password_confirm")
        if password is not None and confirmation is not None and password != confirmation:
            self.add_error("password_confirm", "Las contraseñas no coinciden.")
        return cleaned


class LoginForm(forms.Form):
    identifier = forms.CharField(
        label="Usuario o email",
        max_length=254,
        widget=text_input("username", "Tu usuario o email"),
    )
    password = forms.CharField(
        label="Contraseña",
        strip=False,
        widget=password_input("current-password"),
    )

    def clean_identifier(self):
        value = self.cleaned_data["identifier"]
        try:
            if "@" in value:
                validate_email(value)
            else:
                validate_username(value)
        except ValidationError:
            raise ValidationError("Ingresá un usuario o email válido.", code="invalid_identifier")
        return value.lower()


class ProfileForm(forms.Form):
    username = forms.CharField(
        label="Nombre de usuario",
        max_length=30,
        validators=[validate_username],
        help_text="3 a 30 caracteres. Letras sin acentos, números, punto, guion o guion bajo.",
        widget=text_input("username", "Tu nombre en Plan B"),
    )
