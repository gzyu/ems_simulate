from pydantic import BaseModel
from typing import Any


class BaseResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: Any = None
