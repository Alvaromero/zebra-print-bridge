"""HTTP helpers shared by the Browser Print endpoints (server.py) and the viewer (ui.py)."""


class HttpError(Exception):
    """An error answered with this status and a readable message (web apps show it to the user)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def int_field(body: dict, key: str, default: int = None) -> int:
    """body[key] as an int; missing or empty gives `default`, anything else that is not a number a 400."""
    value = body.get(key)
    if value is None or value == "":
        if default is None:
            raise HttpError(400, f"{key} is required")
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HttpError(400, f"Invalid {key}: {value!r}") from None


def float_field(body: dict, key: str, default: float) -> float:
    value = body.get(key)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise HttpError(400, f"Invalid {key}: {value!r}") from None


def str_field(body: dict, key: str, required: bool = False) -> str:
    """body[key] as stripped text; with `required`, missing or blank is a 400."""
    value = str(body.get(key) or "").strip()
    if required and not value:
        raise HttpError(400, f"{key} is required")
    return value
