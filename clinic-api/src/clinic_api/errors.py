class ClinicError(Exception):
    """A business-rule error, rendered as {"error": {"code", "message"}} by the API."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def not_found(entity: str, entity_id: str) -> ClinicError:
    return ClinicError(404, f"{entity}_not_found", f"No {entity} with id '{entity_id}'.")
