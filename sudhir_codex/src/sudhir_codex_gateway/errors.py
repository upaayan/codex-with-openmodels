"""Sanitized errors that can safely cross the local gateway boundary."""


class GatewayError(Exception):
    """An HTTP-shaped error whose message must not contain credentials."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def as_openai_error(self) -> dict[str, object]:
        return {
            "error": {
                "message": self.message,
                "type": "sudhir_codex_gateway_error",
                "code": self.code,
            }
        }
