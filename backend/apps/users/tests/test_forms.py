from django.test import SimpleTestCase

from apps.users.forms import LoginForm, RegisterForm


class RegisterFormTests(SimpleTestCase):
    def data(self, **overrides):
        return {
            "username": "PlanB_user", "email": "persona@example.com",
            "password": "abcdefghijkl1", "password_confirm": "abcdefghijkl1",
            **overrides,
        }

    def test_valid_registration(self):
        form = RegisterForm(self.data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_each_field_is_required(self):
        for field in self.data():
            with self.subTest(field=field):
                form = RegisterForm(self.data(**{field: ""}))
                self.assertFalse(form.is_valid())
                self.assertIn(field, form.errors)

    def test_username_format_and_boundaries(self):
        for username in ["ab", "a" * 31, "nombre con espacios", "Álvaro", "_nombre", "@nombre", "usuario/otra", "user\nname"]:
            with self.subTest(username=username):
                form = RegisterForm(self.data(username=username))
                self.assertFalse(form.is_valid())
                self.assertIn("username", form.errors)
        for username in ["abc", "a" * 30, "Facundo", "uno_dos.tres-4", "123"]:
            with self.subTest(username=username):
                form = RegisterForm(self.data(username=username))
                self.assertTrue(form.is_valid(), form.errors)

    def test_trim_username_and_normalize_email_preserve_display_case(self):
        form = RegisterForm(self.data(username=" Facundo ", email=" PERSONA@EXAMPLE.COM "))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["username"], "Facundo")
        self.assertEqual(form.cleaned_data["email"], "persona@example.com")

    def test_invalid_email(self):
        form = RegisterForm(self.data(email="correo-invalido"))
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_password_requires_twelve_characters_ascii_letter_and_digit(self):
        for password in ["abcdefghij1", "abcdefghijkl", "123456789012", "áéíóúáéíóú12", "abcdefghijkl١"]:
            with self.subTest(password=password):
                form = RegisterForm(self.data(password=password, password_confirm=password))
                self.assertFalse(form.is_valid())
                self.assertIn("password", form.errors)

    def test_password_at_boundary_is_accepted(self):
        password = "abcdefghijk1"
        form = RegisterForm(self.data(password=password, password_confirm=password))
        self.assertTrue(form.is_valid(), form.errors)

    def test_no_additional_common_or_similarity_rejection(self):
        password = "Password1234"
        form = RegisterForm(self.data(username=password, password=password, password_confirm=password))
        self.assertTrue(form.is_valid(), form.errors)

    def test_password_is_not_trimmed_or_normalized(self):
        password = "  Abcdé 12345  "
        form = RegisterForm(self.data(password=password, password_confirm=password))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["password"], password)

    def test_confirmation_must_match_exactly(self):
        form = RegisterForm(self.data(password_confirm="abcdefghijkl1 "))
        self.assertFalse(form.is_valid())
        self.assertIn("password_confirm", form.errors)

    def test_password_fields_never_render_values(self):
        form = RegisterForm(self.data(email="invalid"))
        self.assertFalse(form.is_valid())
        self.assertNotIn("abcdefghijkl1", str(form["password"]))
        self.assertNotIn("abcdefghijkl1", str(form["password_confirm"]))


class LoginFormTests(SimpleTestCase):
    def test_username_and_email_are_trimmed_and_normalized(self):
        for identifier, expected in [(" FaCuNDo ", "facundo"), (" PERSONA@EXAMPLE.COM ", "persona@example.com")]:
            with self.subTest(identifier=identifier):
                form = LoginForm({"identifier": identifier, "password": "original 123  "})
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(form.cleaned_data["identifier"], expected)
                self.assertEqual(form.cleaned_data["password"], "original 123  ")

    def test_invalid_identifiers(self):
        for identifier in ["", "not an email", "bad@", "ab", "hello/name"]:
            with self.subTest(identifier=identifier):
                form = LoginForm({"identifier": identifier, "password": "AnyPassword123"})
                self.assertFalse(form.is_valid())
                self.assertIn("identifier", form.errors)

    def test_login_does_not_apply_registration_password_policy(self):
        # La identidad la decide el proveedor; el login no vuelve a registrar.
        form = LoginForm({"identifier": "persona", "password": "old"})
        self.assertTrue(form.is_valid(), form.errors)

    def test_password_is_required(self):
        form = LoginForm({"identifier": "persona", "password": ""})
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)
