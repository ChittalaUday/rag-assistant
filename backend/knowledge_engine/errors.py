class AppError(Exception):
    """Domain error rendered as ``{"detail": ...}`` with the given HTTP status."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
