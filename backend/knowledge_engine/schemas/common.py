from pydantic import BaseModel


class OkResponse(BaseModel):
    ok: bool


class ErrorResponse(BaseModel):
    detail: str
