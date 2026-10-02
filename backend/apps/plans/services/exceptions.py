class PlanError(Exception):
    message = "No pudimos completar la operación."

    def __init__(self):
        super().__init__(self.message)


class PlanUnavailable(PlanError):
    message = "No pudimos conectar con tus planes. Volvé a intentar en unos minutos."


class InvalidPlan(PlanError):
    message = "Revisá los datos del plan y el grupo seleccionado."


class PlanNotFound(PlanError):
    message = "No encontramos ese plan."