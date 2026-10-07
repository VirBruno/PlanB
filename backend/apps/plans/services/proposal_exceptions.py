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


class ProposalNotFound(ProposalError):
    message = "No encontramos esa propuesta."