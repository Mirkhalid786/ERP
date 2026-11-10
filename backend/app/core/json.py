"""JSON helpers."""
from decimal import Decimal
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def decimal_json(data: Any, status_code: int = 200) -> JSONResponse:
    """
    Serialise plain dict/list results with Decimal money values as exact strings
    ("84.00"), the same as Pydantic response models do. FastAPI's default encoder
    would turn them into binary floats.
    """
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(data, custom_encoder={Decimal: lambda d: format(d, "f")}),
    )
