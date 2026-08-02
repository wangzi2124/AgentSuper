"""API 通用依赖与鉴权辅助函数。"""

from fastapi import HTTPException, Request


def require_admin(request: Request) -> None:
    """管理端接口鉴权。

    若配置了 ADMIN_TOKEN，则要求请求头携带 `Authorization: Bearer <token>`；
    未配置 ADMIN_TOKEN 时跳过校验，保持本地单用户开发模式。
    """
    from app.config import settings

    token = settings.admin_token
    if not token:
        return
    auth = request.headers.get("Authorization", "")
    provided = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
    if not provided or provided != token:
        raise HTTPException(status_code=401, detail="Unauthorized")
