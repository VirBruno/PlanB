class ProposalError(Exception):
    message = "No pudimos completar la operación de la propuesta."

    def __init__(self):
        super().__init__(self.message)


class ProposalUnavailable(ProposalError):
    message = "No pudimos conectar con las propuestas. Volvé a intentar en unos minutos."


class InvalidProposal(ProposalError):
    message = "Revisá los datos de la propuesta."


class ProposalAlreadyExists(ProposalError):
    message = "Ya creaste una propuesta para este plan."


class ElectionSchemaUnavailable(ProposalUnavailable):
    message = "Aplicá la migración de métodos de elección en Supabase para habilitar esta función."


class ElectionFlexibilityUnavailable(ProposalUnavailable):
    message = "Aplicá la migración flexible de elecciones para cambiar métodos o votos existentes."


class ProposalNotFound(ProposalError):
    message = "No encontramos esa propuesta."