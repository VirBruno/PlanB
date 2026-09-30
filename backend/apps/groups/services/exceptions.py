class GroupError(Exception):
    message = "No pudimos completar la operación."

    def __init__(self):
        super().__init__(self.message)


class GroupUnavailable(GroupError):
    message = "No pudimos conectar con tus grupos. Volvé a intentar en unos minutos."


class InvalidGroup(GroupError):
    message = "Revisá el nombre y la descripción del grupo."


class GroupNotFound(GroupError):
    message = "No encontramos ese grupo."
