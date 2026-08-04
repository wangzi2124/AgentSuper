"""API 通用依赖与鉴权辅助函数。"""

from fastapi import HTTPException, Request


def require_admin(request: Request) -> None:
    """管理端接口鉴权。

    配置了 ADMIN_TOKEN 时要求请求头携带 `Authorization: Bearer <token>`；
    未配置 ADMIN_TOKEN 时仅允许本机来源（127.0.0.1 / ::1 / localhost）访问，
    阻止局域网/公网上的未授权调用（ADMIN_TOKEN 为空时管理接口不可远程操作）。
    """
    from app.config import settings

    token = settings.admin_token
    auth = request.headers.get("Authorization", "")
    provided = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
    if token:
        if not provided or provided != token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return
    host = (request.client.host if request.client else "") or ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: ADMIN_TOKEN is not configured; management endpoints only allow localhost",
        )
