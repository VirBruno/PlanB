"""Errores públicos controlados: no conservar respuestas del proveedor."""


class AuthError(Exception):
    message = "No pudimos completar la operación. Intentá nuevamente."

    def __init__(self):
        super().__init__(self.message)


class InvalidCredentials(AuthError):
    message = "Usuario/email o contraseña incorrectos."


class UsernameUnavailable(AuthError):
    message = "Ese nombre de usuario ya está en uso."


class ServiceUnavailable(AuthError):
    message = "El servicio no está disponible en este momento. Intentá nuevamente."


class RegistrationRejected(AuthError):
    message = "No pudimos completar el registro. Revisá los datos e intentá nuevamente."


class SessionExpired(AuthError):
    message = "Tu sesión venció. Iniciá sesión nuevamente."


class ConfirmationInvalid(AuthError):
    message = "El enlace de confirmación no es válido o venció."
