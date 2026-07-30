from __future__ import annotations

from collections.abc import Mapping

from fastapi.responses import JSONResponse


def config_validation_response(field_errors: Mapping[str, str]) -> JSONResponse:
    """返回不受 FastAPI detail 包裹的字段级配置校验响应。"""
    return JSONResponse(status_code=422, content={"field_errors": dict(field_errors)})
