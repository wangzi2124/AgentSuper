"""用户身份注册/令牌 API。

- GET  /api/auth/status   是否启用身份签名
- POST /api/auth/register 注册 user_id + device_secret（首次信任绑定）
- POST /api/auth/token    用 device_secret 换取短期签名 token

启用方式：.env 配置 AUTH_TOKEN_SECRET。未启用时这些接口保持可用（幂等返回状态）。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import auth as auth_service

router = APIRouter()


class RegisterRequest(BaseModel):
    user_id: str
    device_secret: str


class TokenRequest(BaseModel):
    user_id: str
    device_secret: str


@router.get("/status")
async def auth_status():
    """返回身份签名是否启用（前端据此决定是否注册/带 token）。"""
    return {"enabled": auth_service.enabled()}


@router.post("/register")
async def register(body: RegisterRequest):
    """首次注册 user_id 与设备密钥（trust-on-first-use）。"""
    ok, err = auth_service.register(body.user_id, body.device_secret)
    if not ok:
        status = 409 if "已" in err else 400
        raise HTTPException(status_code=status, detail=err)
    return {"status": "registered", "user_id": body.user_id}


@router.post("/token")
async def issue_token(body: TokenRequest):
    """用注册设备密钥换取签名 token。"""
    if not auth_service.verify_device(body.user_id, body.device_secret):
        raise HTTPException(status_code=401, detail="user_id 未注册或 device_secret 不匹配")
    token, exp = auth_service.issue_token(body.user_id)
    return {"token": token, "expires_at": exp}
