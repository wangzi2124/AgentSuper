"""用户身份注册/登录/令牌 API。

- GET  /api/auth/status              是否启用身份签名
- POST /api/auth/register            设备身份注册（trust-on-first-use，历史兼容）
- POST /api/auth/token               用设备密钥换取签名 token
- POST /api/auth/account/register    注册账号（用户名 + 密码），成功后自动签发 token
- POST /api/auth/account/login       账号登录，签发签名 token
- GET  /api/auth/account/me          返回当前用户信息（需 X-User-Id + X-Auth-Token）

启用方式：.env 配置 AUTH_TOKEN_SECRET。未启用时这些接口保持可用（幂等返回状态）。
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app import auth as auth_service
from .responses import ok

router = APIRouter()


class RegisterRequest(BaseModel):
    user_id: str
    device_secret: str


class TokenRequest(BaseModel):
    user_id: str
    device_secret: str


class AccountRegisterRequest(BaseModel):
    username: str
    password: str


class AccountLoginRequest(BaseModel):
    username: str
    password: str


@router.get("/status")
async def auth_status():
    """返回身份签名是否启用（前端据此决定是否需要登录）。"""
    return ok({"enabled": auth_service.enabled()})


@router.post("/register")
async def register(body: RegisterRequest):
    """首次注册 user_id 与设备密钥（trust-on-first-use）。"""
    ok_flag, err = auth_service.register(body.user_id, body.device_secret)
    if not ok_flag:
        status = 409 if "已" in err else 400
        raise HTTPException(status_code=status, detail=err)
    return ok({"status": "registered", "user_id": body.user_id})


@router.post("/token")
async def issue_token(body: TokenRequest):
    """用注册设备密钥换取签名 token。"""
    if not auth_service.verify_device(body.user_id, body.device_secret):
        raise HTTPException(status_code=401, detail="user_id 未注册或 device_secret 不匹配")
    token, exp = auth_service.issue_token(body.user_id)
    return ok({"token": token, "expires_at": exp})


@router.post("/account/register")
async def account_register(body: AccountRegisterRequest):
    """注册账号（用户名 + 密码），成功后自动签发 token（自动登录）。"""
    ok_flag, err, uid = auth_service.register_account(body.username, body.password)
    if not ok_flag:
        status = 409 if "已被注册" in err else 400
        raise HTTPException(status_code=status, detail=err)
    token, exp = auth_service.issue_token(uid)
    return ok({
        "status": "registered",
        "user_id": uid,
        "username": body.username.strip(),
        "token": token,
        "expires_at": exp,
    })


@router.post("/account/login")
async def account_login(body: AccountLoginRequest):
    """账号登录：校验用户名密码并签发签名 token。"""
    uid = auth_service.authenticate_account(body.username, body.password)
    if not uid:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token, exp = auth_service.issue_token(uid)
    info = auth_service.account_info(uid) or {}
    return ok({
        "user_id": uid,
        "username": info.get("username", body.username.strip()),
        "token": token,
        "expires_at": exp,
    })


@router.get("/account/me")
async def account_me(request: Request):
    """返回当前用户信息（中间件放行 /api/auth/*，此处手动校验 token）。

    兼容两类身份：account（用户名）与 device（历史随机 user_id）。
    """
    uid = request.headers.get("X-User-Id", "").strip()
    token = request.headers.get("X-Auth-Token", "").strip()
    if not uid or not auth_service.verify_token(uid, token):
        raise HTTPException(status_code=401, detail="未登录或会话已过期，请重新登录")
    info = auth_service.account_info(uid)
    if info is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return ok(info)
