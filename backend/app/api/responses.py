"""统一 API 响应封装与错误处理。

成功：{"code": 0, "message": "ok", "data": ...}
失败：{"code": <业务码>, "message": <提示>, "data": null}，同时保留 detail 字段兼容旧调用方。

- 业务异常使用 ApiError 抛出，由 main.py 注册的全局异常处理器转换为统一响应。
- 既有 HTTPException / 参数校验异常同样被统一。
"""

from typing import Any

from fastapi.responses import JSONResponse


class ApiError(Exception):
    """业务异常：携带统一错误码、提示消息与 HTTP 状态码。"""

    def __init__(
        self,
        code: int = 1,
        message: str = "error",
        status: int = 400,
        data: Any = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.data = data


def api_result(code: int, message: str, data: Any = None) -> dict:
    """构造统一响应体。code=0 表示成功。"""
    return {"code": code, "message": message, "data": data}


def ok(data: Any = None, message: str = "ok") -> dict:
    return api_result(0, message, data)


def fail(code: int = 1, message: str = "error", data: Any = None) -> dict:
    return api_result(code, message, data)


def error_response(
    code: int, message: str, status: int, data: Any = None
) -> JSONResponse:
    """构造统一错误响应，附带 detail 别名兼容旧调用方。"""
    body = api_result(code, message, data)
    body["detail"] = message
    return JSONResponse(status_code=status, content=body)
